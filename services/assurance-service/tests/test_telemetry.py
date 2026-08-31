"""Telemetry utilities: W3C trace context, correlation, redaction, safe labels."""
import re

import pytest

from app.domain.identity import assert_safe_labels, normalize_alert_name, validate_label
from app.domain.exceptions import CardinalityError

from shared.python.isp_shared import telemetry as tel


def test_traceparent_roundtrip():
    trace_id = tel.generate_trace_id()
    span_id = tel.generate_span_id()
    tp = tel.traceparent(trace_id, span_id, flags=1)
    parsed = tel.parse_traceparent(tp)
    assert parsed["version"] == "00"
    assert parsed["trace_id"] == trace_id
    assert parsed["parent_id"] == span_id
    assert parsed["flags"] == "01"


def test_extract_and_inject_headers():
    headers = {"traceparent": tel.traceparent(tel.generate_trace_id(), tel.generate_span_id())}
    parsed = tel.extract_from_headers(headers)
    assert parsed["trace_id"] is not None
    out = tel.inject_headers(parsed["trace_id"], parsed["span_id"])
    assert "traceparent" in out
    assert out["traceparent"].split("-")[1] == parsed["trace_id"]


def test_trace_span_context():
    trace_id = tel.generate_trace_id()
    with tel.TraceSpan("op", trace_id=trace_id) as span:
        assert tel.current_trace_id.get() == trace_id
        assert span.span_id
    assert tel.current_span_id.get() is None


def test_correlation_set_reset():
    token = tel.set_correlation("corr-123")
    assert tel.current_correlation_id.get() == "corr-123"
    tel.reset_correlation(token)
    assert tel.current_correlation_id.get() is None


def test_redact_hides_sensitive_values():
    assert tel.redact("password=hunter2") == "password=••••"
    assert tel.redact("Authorization: Bearer abcdef123456") == "Authorization=•••• abcdef123456"
    assert tel.redact("api_key=sk-live-1234") == "api_key=••••"
    assert tel.redact("ordinary text") == "ordinary text"


def test_redaction_filter_redacts_log_record():
    import logging
    record = logging.LogRecord("x", logging.INFO, "", 0, "token=SECRET123", (), None)
    assert tel.redact(record.getMessage()) != record.getMessage()


def test_assert_safe_label_ok():
    assert_safe_labels({"service": "aaa", "environment": "prod", "severity": "CRITICAL"})


def test_assert_safe_label_rejects_high_cardinality():
    with pytest.raises(CardinalityError):
        assert_safe_labels({"customer_id": "cust-123"})
    with pytest.raises(CardinalityError):
        assert_safe_labels({"subscriber_id": "sub-1"})
    with pytest.raises(CardinalityError):
        assert_safe_labels({"order_id": "ord-1"})
    with pytest.raises(CardinalityError):
        assert_safe_labels({"ip_address": "10.0.0.1"})
    with pytest.raises(CardinalityError):
        assert_safe_labels({"mac_address": "aa:bb:cc:dd:ee:ff"})


def test_validate_label_returns_or_raises():
    assert validate_label("service", "aaa") is None  # valid, returns None
    with pytest.raises(CardinalityError):
        validate_label("order_uuid", "x")


def test_normalize_alert_name():
    assert normalize_alert_name("CPU High") == "cpu_high"
    assert normalize_alert_name("NAS   Down!!") == "nas_down"
