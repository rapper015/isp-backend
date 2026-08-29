"""Adapter contracts for cross-bounded-context integration.

The workforce service never touches another service's database. Every side
effect or data fetch is performed through an adapter. Fakes are deterministic
for tests; real adapters are thin HTTP/gateway clients declared but wired only
via environment configuration. The technician API can never execute arbitrary
RouterOS commands or edit FreeRADIUS/RADIUS — remote activation goes through
OSS/AAA/Network Control adapters."""
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
class ActionResult:
    ok: bool
    reference: str | None = None
    detail: dict = field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False


def ok_result(output: dict | None = None, **extra: Any) -> StepResult:
    return StepResult(ok=True, output={**(output or {}), **extra})


def fail_result(error_code: str, error_detail: str, retryable: bool = False) -> StepResult:
    return StepResult(ok=False, error_code=error_code, error_detail=error_detail, retryable=retryable)


class Adapter:
    name = "base"


ADAPTERS: dict[str, Adapter] = {}


def register(cls):
    ADAPTERS[cls.name] = cls()
    return cls


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError as error:
        raise RuntimeError(f"no adapter registered for {name!r}") from error


def reset_adapters() -> None:
    for name, instance in list(ADAPTERS.items()):
        ADAPTERS[name] = type(instance)()
