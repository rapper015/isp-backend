"""Adapter contracts for cross-bounded-context integration.

OSS never talks to other services' databases; every side effect is performed
through an adapter. Fakes are deterministic for tests; real adapters are thin
HTTP/gateway clients (declared, not yet wired to live endpoints)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AdapterError(Exception):
    pass


class RetryableAdapterError(AdapterError):
    pass


class NonRetryableAdapterError(AdapterError):
    pass


@dataclass
class StepResult:
    ok: bool
    output: dict = field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)


def ok_result(output: dict | None = None, **extra: Any) -> StepResult:
    return StepResult(ok=True, output={**(output or {}), **extra})


def fail_result(error_code: str, error_detail: str, retryable: bool = False) -> StepResult:
    return StepResult(ok=False, error_code=error_code, error_detail=error_detail, retryable=retryable)


class Adapter:
    name = "base"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Adapter {self.name}>"


ADAPTERS: dict[str, Adapter] = {}


def register(cls):
    """Class decorator: register an adapter instance under cls.name."""
    ADAPTERS[cls.name] = cls()
    return cls


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError as error:
        raise RuntimeError(f"no adapter registered for {name!r}") from error
