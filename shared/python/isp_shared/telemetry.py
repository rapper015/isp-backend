"""Shared observability helpers for the ISP platform (Milestone 9).

Implements the W3C Trace Context wire contract (traceparent) so context can be
propagated across HTTP and RabbitMQ without adding an SDK dependency, plus
structured JSON logging with standard fields and centralized redaction.

The assurance-service is the governance layer; raw metrics/logs/traces are
stored by Prometheus/Loki/Tempo via an OpenTelemetry Collector — this package
only helps instrument and propagate, it never stores telemetry.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

_SERVICE = os.getenv("SERVICE_NAME", "unknown")
_ENV = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development"))

# W3C Trace Context
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

current_trace_id: ContextVar[str | None] = ContextVar("assurance_trace_id", default=None)
current_span_id: ContextVar[str | None] = ContextVar("assurance_span_id", default=None)
current_correlation_id: ContextVar[str | None] = ContextVar("assurance_correlation_id", default=None)
current_tenant_id: ContextVar[str | None] = ContextVar("assurance_tenant_id", default=None)

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------
def generate_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars (128-bit)


def generate_span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex chars (64-bit)


# ---------------------------------------------------------------------------
# W3C Trace Context (traceparent)
# ---------------------------------------------------------------------------
def traceparent(trace_id: str | None = None, span_id: str | None = None, flags: int = 1) -> str:
    tid = (trace_id or current_trace_id.get() or generate_trace_id())
    sid = (span_id or current_span_id.get() or generate_span_id())
    if not _TRACE_ID_RE.match(tid):
        tid = generate_trace_id()
    if not _SPAN_ID_RE.match(sid):
        sid = generate_span_id()
    return f"00-{tid}-{sid}-{flags:02x}"


def parse_traceparent(value: str | None) -> dict | None:
    """Parse a traceparent header into {'version','trace_id','parent_id','flags'}."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, parent_id, flags = parts
    if not _TRACE_ID_RE.match(trace_id) or not _SPAN_ID_RE.match(parent_id):
        return None
    return {"version": version, "trace_id": trace_id, "parent_id": parent_id, "flags": flags}


def extract_from_headers(headers: dict | None) -> dict:
    """Extract trace context from HTTP/RabbitMQ headers (case-insensitive)."""
    headers = headers or {}
    def _get(*names: str):
        for name in names:
            for key, value in headers.items():
                if str(key).lower() == name.lower():
                    return str(value)
        return None
    parsed = parse_traceparent(_get("traceparent", "trace_parent"))
    if parsed is None:
        return {"trace_id": None, "span_id": None, "baggage": None}
    return {"trace_id": parsed["trace_id"], "span_id": parsed["parent_id"],
            "baggage": _get("baggage")}


def inject_headers(trace_id: str | None = None, span_id: str | None = None) -> dict:
    return {"traceparent": traceparent(trace_id, span_id)}


class TraceSpan:
    """Context manager that sets trace/span ids for the duration of a block."""

    def __init__(self, name: str, *, trace_id: str | None = None, parent_span_id: str | None = None):
        self.name = name
        self.trace_id = trace_id or current_trace_id.get() or generate_trace_id()
        self.span_id = generate_span_id()
        self.parent_span_id = parent_span_id or current_span_id.get()
        self._tokens = None

    def __enter__(self):
        self._tokens = (current_trace_id.set(self.trace_id), current_span_id.set(self.span_id))
        return self

    def __exit__(self, *exc):
        if self._tokens:
            current_trace_id.reset(self._tokens[0])
            current_span_id.reset(self._tokens[1])
        return False


def set_correlation(correlation_id: str | None):
    token = None
    if correlation_id:
        token = current_correlation_id.set(correlation_id)
    return token


def reset_correlation(token) -> None:
    if token is not None:
        current_correlation_id.reset(token)


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
_SENSITIVE_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|radius[_-]?secret|"
    r"connection[_-]?request[_-]?password|pppoe[_-]?password|wif[_-]?password|"
    r"bank[_-]?ref|account[_-]?number|otp|pan|aadhaar|ssn)\s*[:=]\s*[\"']?[^\s,\"']+")
_FORBIDDEN_IN_TELEMETRY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|radius[_-]?secret|pan|aadhaar)")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", None) or _SERVICE,
            "environment": getattr(record, "environment", None) or _ENV,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None) or current_trace_id.get(),
            "span_id": getattr(record, "span_id", None) or current_span_id.get(),
            "correlation_id": getattr(record, "correlation_id", None) or current_correlation_id.get(),
            "tenant_id": getattr(record, "tenant_id", None) or current_tenant_id.get(),
        }
        for key in ("operation", "entity_type", "entity_id", "workflow_id", "result",
                    "duration_ms", "retry_count", "error_class", "instance"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        exc = record.exc_info
        if exc and exc[0] is not None:
            payload["exception"] = f"{exc[0].__name__}: {exc[1]}"
        return json.dumps(payload, default=str)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _SENSITIVE_RE.search(message):
            record.msg = _SENSITIVE_RE.sub(r"\1=••••", message)
        return True


def configure_logging(*, level: str | None = None, json_output: bool | None = None,
                      service: str | None = None) -> None:
    """Idempotent logging configuration. Reads LOG_LEVEL / LOG_JSON when unset."""
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    json_output = json_output if json_output is not None else \
        os.getenv("LOG_JSON", "false").lower() in ("1", "true", "yes")
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        root.handlers.clear()
    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s trace=%(trace_id)s span=%(span_id)s corr=%(correlation_id)s %(message)s"))
    root.addHandler(handler)
    root.addFilter(RedactionFilter())
    if service:
        logging.getLogger().service = service


def get_logger(name: str, **defaults) -> logging.Logger:
    logger = logging.getLogger(f"{_SERVICE}.{name}")
    for key, value in defaults.items():
        setattr(logger, key, value)
    return logger


def redact(text: str) -> str:
    return _SENSITIVE_RE.sub(r"\1=••••", text or "")


def assert_safe_label(name: str, value: str) -> None:
    """Cardinality/secret policy for telemetry labels. Rejects high-cardinality
    or sensitive identifiers as metric labels (they belong in logs/traces)."""
    if not value:
        return
    lowered = value.lower()
    forbidden_high_cardinality = (
        "customer", "subscriber", "username", "ticket", "order-", "uuid", "trace", "session",
        "invoice", "serial", "mac:", "ip:", "exception", "error-message",
    )
    if _FORBIDDEN_IN_TELEMETRY.search(value):
        raise ValueError(f"label {name!r} contains a sensitive value")
    if any(marker in lowered for marker in forbidden_high_cardinality):
        raise ValueError(f"label {name!r} has high-cardinality value")


def set_tenant(tenant_id: str | None):
    token = current_tenant_id.set(tenant_id) if tenant_id else None
    return token


def reset_tenant(token) -> None:
    if token is not None:
        current_tenant_id.reset(token)


def now_ms() -> int:
    return int(time.time() * 1000)
