"""Run with PYTHONPATH=shared/python python -m unittest discover -s shared/tests."""
import asyncio
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from isp_shared import observability as obs
from isp_shared import telemetry as tel


class LoggingTests(unittest.TestCase):
    def test_parameterized_redaction_and_context(self):
        record = logging.LogRecord("child", logging.ERROR, __file__, 1, "password=%s", ("secret-value",), None)
        tel.RedactionFilter().filter(record)
        value = json.loads(tel.JsonFormatter().format(record))
        self.assertNotIn("secret-value", json.dumps(value))
        self.assertIsNone(value["correlation_id"])

    def test_quoted_values_bearer_and_connection_strings(self):
        value = tel.redact('password="two words" {"refresh_token": "abc def"} Bearer private-token postgresql://user:private-pass@db')
        for secret in ("two words", "abc def", "private-token", "private-pass"):
            self.assertNotIn(secret, value)

    def test_exception_values_are_not_logged(self):
        try:
            raise ValueError("arbitrary-private-value")
        except ValueError:
            import sys
            record = logging.LogRecord("test", logging.ERROR, __file__, 1, "operation.failed", (), sys.exc_info())
        result = json.loads(tel.JsonFormatter().format(record))
        self.assertEqual(result["error_class"], "ValueError")
        self.assertTrue(result["stacktrace"])
        self.assertNotIn("arbitrary-private-value", json.dumps(result))

    def test_nested_extra_secrets_are_redacted(self):
        record = logging.LogRecord("child", logging.INFO, __file__, 1, "operation.done", (), None)
        record.result = {"credentials": [{"password": "nested-private", "email": "private@example.test"}]}
        result = tel.JsonFormatter().format(record)
        self.assertNotIn("nested-private", result)
        self.assertNotIn("private@example.test", result)

    def test_text_logging_does_not_require_supplied_context(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(tel.RedactionFilter())
        handler.setFormatter(tel.SafeTextFormatter("%(trace_id)s %(correlation_id)s %(message)s"))
        handler.handle(logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None))
        self.assertIn("hello", output.getvalue())

    def test_valid_traceparent_only(self):
        valid = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
        self.assertIsNotNone(tel.parse_traceparent(valid))
        for value in (valid.replace("00-", "ff-", 1), valid[:-2] + "xy", valid.replace("a" * 32, "0" * 32)):
            self.assertIsNone(tel.parse_traceparent(value))

    def test_sentry_event_allowlist(self):
        event = {"event_id": "123", "request": {"data": "secret"}, "user": {"email": "private"},
                 "breadcrumbs": {"values": [{"message": "secret"}]}, "extra": {"password": "secret"},
                 "exception": {"values": [{"type": "ValueError", "value": "private-exception",
                     "stacktrace": {"frames": [{"filename": "module.py", "lineno": 4, "vars": {"password": "secret"}, "context_line": "secret"}]}}]}}
        result = obs.safe_event(event)
        self.assertEqual(result["event_id"], "123")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("private-exception", json.dumps(result))
        self.assertNotIn("request", result)


def make_app():
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def ok(item_id: str):
        await asyncio.sleep(0.005)
        return {"correlation_id": tel.current_correlation_id.get(), "headers": obs.diagnostic_headers()}

    @app.get("/bad")
    async def bad():
        raise HTTPException(422, "tenant_id required")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("never-send-this-private-value")

    with patch.dict(os.environ, {"SENTRY_DSN": ""}):
        obs.install_observability(app, "test-service")
    return app


class RequestTests(unittest.TestCase):
    def test_success_validation_error_and_unhandled_exception(self):
        client = TestClient(make_app(), raise_server_exceptions=False)
        for path, status in (("/items/one?password=private", 200), ("/bad", 422), ("/crash", 500)):
            response = client.get(path, headers={"X-Correlation-ID": "test-request"})
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers["X-Correlation-ID"], "test-request")
            if status == 500:
                self.assertEqual(response.json()["correlation_id"], "test-request")
                self.assertNotIn("never-send", response.text)
            if status == 422:
                self.assertEqual(response.json()["detail"], "tenant_id required")
        self.assertIsNone(tel.current_correlation_id.get())

    def test_invalid_ids_are_replaced(self):
        client = TestClient(make_app())
        response = client.get("/items/one", headers={"X-Correlation-ID": "bad value"})
        self.assertNotEqual(response.headers["X-Correlation-ID"], "bad value")
        self.assertEqual(len(response.headers["X-Correlation-ID"]), 32)

    def test_concurrent_requests_do_not_share_context(self):
        app = make_app()

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                responses = await asyncio.gather(*[client.get("/items/one", headers={"X-Correlation-ID": f"req-{i}"}) for i in range(12)])
                for i, response in enumerate(responses):
                    self.assertEqual(response.json()["correlation_id"], f"req-{i}")
                    self.assertEqual(response.json()["headers"]["X-Correlation-ID"], f"req-{i}")
        asyncio.run(run())

    def test_error_event_reaches_sentry_transport_without_private_data(self):
        try:
            import sentry_sdk
            from sentry_sdk.transport import Transport
            from sentry_sdk.integrations.logging import LoggingIntegration
        except ImportError:
            self.skipTest("install shared/runtime/requirements.txt for Sentry transport verification")
        events = []

        class MemoryTransport(Transport):
            def capture_envelope(self, envelope):
                for item in envelope.items:
                    if item.type == "event":
                        events.append(item.payload.json)

        app = make_app()
        with sentry_sdk.init(dsn="https://public@example.invalid/1", transport=MemoryTransport,
                default_integrations=False, auto_enabling_integrations=False,
                integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
                before_send=obs.safe_event, include_local_variables=False):
            TestClient(app, raise_server_exceptions=False).get("/crash", headers={"X-Correlation-ID": "sentry-test"})
            sentry_sdk.flush()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tags"]["correlation_id"], "sentry-test")
        self.assertNotIn("never-send-this-private-value", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
