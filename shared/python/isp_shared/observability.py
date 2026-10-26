"""Common request diagnostics and opt-in Sentry error tracking.

No request bodies, credentials, query strings or customer profiles are exported.
HTTP errors keep their existing API contract; support can use X-Correlation-ID.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid

from .telemetry import (
    configure_logging, current_correlation_id, current_span_id,
    current_tenant_id, current_trace_id, generate_span_id, generate_trace_id,
    parse_traceparent, traceparent,
)

logger = logging.getLogger("isp.requests")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SENTRY_READY = False


def safe_event(event, hint=None):
    """Allowlist diagnostics sent to Sentry, including frames without locals.

    Exception messages can contain SQL parameters, passwords or customer data.
    Preserve types and frame locations for grouping, not arbitrary error text.
    """
    allowed = {key: event[key] for key in (
        "event_id", "timestamp", "level", "platform", "logger", "release",
        "environment", "server_name", "sdk", "type",
    ) if key in event}
    tags = event.get("tags") or {}
    allowed["tags"] = {key: value for key, value in tags.items() if key in {
        "service", "correlation_id", "trace_id", "operation", "error_code",
    }}
    record = (hint or {}).get("log_record")
    for key in ("service", "operation", "error_code", "correlation_id", "trace_id"):
        value = getattr(record, key, None)
        if value is not None:
            allowed["tags"][key] = str(value)
    for key, context in (("correlation_id", current_correlation_id), ("trace_id", current_trace_id)):
        if context.get():
            allowed["tags"][key] = context.get()
    values = []
    for item in (event.get("exception") or {}).get("values", []):
        values.append({
            "type": item.get("type", "Exception"),
            "value": "Exception details omitted; inspect the correlated service operation.",
            "stacktrace": {"frames": [
                {key: frame[key] for key in ("filename", "module", "function", "lineno", "in_app") if key in frame}
                for frame in (item.get("stacktrace") or {}).get("frames", [])
            ]},
        })
    if values:
        allowed["exception"] = {"values": values}
    else:
        allowed["message"] = "Backend operation failed"
        # Keep distinct handled errors grouped by stable code, never customer IDs.
        allowed["fingerprint"] = [str(allowed["tags"].get(key, "unknown")) for key in ("service", "operation", "error_code")]
    return allowed


def configure_observability(service: str) -> None:
    global _SENTRY_READY
    configure_logging(service=service)
    if _SENTRY_READY or not os.getenv("SENTRY_DSN", "").strip():
        return
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.getenv("ENVIRONMENT", "development"),
        release=os.getenv("APP_RELEASE") or None,
        server_name=service,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
        before_send=safe_event,
    )
    sentry_sdk.set_tag("service", service)
    _SENTRY_READY = True


class RequestDiagnostics:
    """Pure ASGI middleware: request isolation, completion logs and support IDs."""

    def __init__(self, app, service: str):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-correlation-id", b"").decode("latin1")
        correlation_id = supplied if _SAFE_ID.fullmatch(supplied) else uuid.uuid4().hex
        parsed = parse_traceparent(headers.get(b"traceparent", b"").decode("latin1"))
        trace_id = parsed["trace_id"] if parsed else generate_trace_id()
        span_id = generate_span_id()
        tokens = [(var, var.set(value)) for var, value in (
            (current_correlation_id, correlation_id), (current_trace_id, trace_id),
            (current_span_id, span_id), (current_tenant_id, None),
        )]
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        # Downstream legacy middleware must see the same generated ID.
        scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"x-correlation-id"] + [(b"x-correlation-id", correlation_id.encode())]
        status = 500
        failed = False
        started = time.monotonic()

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-correlation-id"] + [(b"x-correlation-id", correlation_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            failed = True
            logger.exception("request.unhandled", extra={"service": self.service, "operation": _operation(scope)})
            raise
        finally:
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
            if failed:
                level = logging.INFO  # The exception above already creates one error event.
            # Health polling should not dominate operator logs.
            if scope.get("path") not in {"/health", "/metrics"} or status >= 400:
                logger.log(level, "request.completed", extra={
                    "service": self.service, "operation": _operation(scope),
                    "status_code": status, "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })
            for var, token in reversed(tokens):
                var.reset(token)


def _operation(scope):
    route = scope.get("route")
    return f"{scope.get('method', 'HTTP')} {getattr(route, 'path', '<unmatched>')}"


def install_observability(app, service: str) -> None:
    if getattr(app.state, "observability_installed", False):
        return
    configure_observability(service)
    app.add_middleware(RequestDiagnostics, service=service)
    # Starlette's outer server-error handler emits 500 responses outside user
    # middleware. Add the ID explicitly here without exposing exception text.
    from starlette.responses import JSONResponse

    async def unhandled(request, exc):
        correlation_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        return JSONResponse({"detail": "An unexpected server error occurred", "correlation_id": correlation_id},
                            status_code=500, headers={"X-Correlation-ID": correlation_id})

    if Exception not in app.exception_handlers and 500 not in app.exception_handlers:
        app.add_exception_handler(Exception, unhandled)
    app.state.observability_installed = True


def diagnostic_headers() -> dict[str, str]:
    """Explicit propagation for internal HTTP calls; never forwards auth."""
    headers = {"traceparent": traceparent()}
    if current_correlation_id.get():
        headers["X-Correlation-ID"] = current_correlation_id.get()
    return headers
