"""Integration adapter base: step results, action results, registration and
circuit breaker. Raw GenieACS HTTP calls never leak into views or tasks."""
from __future__ import annotations

import time
from typing import Any

_ADAPTERS: dict[str, type] = {}


class AdapterError(Exception):
    def __init__(self, message: str, *, code: str = "adapter_error", retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class RetryableAdapterError(AdapterError):
    pass


class NonRetryableAdapterError(AdapterError):
    def __init__(self, message: str, *, code: str = "adapter_error"):
        super().__init__(message, code=code, retryable=False)


class StepResult:
    def __init__(self, ok: bool, output: dict | None = None, error_code: str | None = None,
                 error_detail: str | None = None, retryable: bool = True):
        self.ok = ok
        self.output = output or {}
        self.error_code = error_code
        self.error_detail = error_detail
        self.retryable = retryable


class ActionResult:
    def __init__(self, ok: bool, reference: str | None = None, detail: dict | None = None,
                 error_code: str | None = None, error_detail: str | None = None, retryable: bool = True):
        self.ok = ok
        self.reference = reference
        self.detail = detail or {}
        self.error_code = error_code
        self.error_detail = error_detail
        self.retryable = retryable


def ok_result(output: dict | None = None, **extra: Any) -> StepResult:
    return StepResult(ok=True, output={**(output or {}), **extra})


def fail_result(error_code: str, error_detail: str, retryable: bool = False) -> StepResult:
    return StepResult(ok=False, error_code=error_code, error_detail=error_detail, retryable=retryable)


class Adapter:
    name = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._breaker_open_until = 0.0
        self._failures = 0

    def _guard(self) -> None:
        if time.time() < self._breaker_open_until:
            raise AdapterError("circuit breaker open", code="circuit_open", retryable=True)

    def _record(self, ok: bool, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        if ok:
            self._failures = 0
            self._breaker_open_until = 0.0
        else:
            self._failures += 1
            if self._failures >= failure_threshold:
                self._breaker_open_until = time.time() + cooldown_seconds


def register(cls):
    _ADAPTERS[cls.name] = cls
    return cls


def get_adapter(name: str, config: dict | None = None) -> Adapter:
    cls = _ADAPTERS.get(name)
    if cls is None:
        raise AdapterError(f"no adapter registered for {name!r}", code="no_adapter", retryable=False)
    return cls(config)


def reset_adapters() -> None:
    for cls in _ADAPTERS.values():
        if hasattr(cls, "reset"):
            cls.reset()
