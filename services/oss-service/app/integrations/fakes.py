"""Deterministic fakes and fault injection helpers for tests.

The adapters in this package are already deterministic in-memory fakes. This
module adds a FaultInjector so tests can make an adapter method fail a fixed
number of times (to exercise saga retries) or permanently (to exercise
compensation and manual intervention).
"""
from __future__ import annotations

import itertools
from typing import Callable

from .base import ADAPTERS, RetryableAdapterError
from . import aaa_client, crm_client, ipam_client, network_client, nas_client, nms_client, bss_client, workforce_client  # noqa: F401  (registration side effects)


def reset_adapters() -> None:
    """Reset module-level fake state between tests (and all injectors)."""
    for module in (aaa_client, ipam_client, network_client, workforce_client):
        for attr in ("_COUNTER", "_CREATED", "_USED_ONT"):
            if hasattr(module, attr):
                if attr == "_COUNTER":
                    setattr(module, attr, itertools.count(1))
                else:
                    setattr(module, attr, set())
    for injector in list(_INJECTORS):
        injector.reset()


_INJECTORS: list["FaultInjector"] = []


class FaultInjector:
    """Wraps a registered adapter's methods with failure injection.

    Usage:
        injector = FaultInjector()
        injector.fail_times("crm", "validate_customer", times=2, retryable=True)
        ... run saga ...   # first two calls fail, third succeeds
        injector.reset()
    """

    def __init__(self) -> None:
        self._remaining: dict[tuple[str, str], int] = {}
        self._permanent: dict[tuple[str, str], bool] = {}
        self._original: dict[tuple[str, str], Callable] = {}
        self._patched: dict[tuple[str, str], Callable] = {}
        _INJECTORS.append(self)

    def fail_times(self, adapter_name: str, method: str, times: int = 1, retryable: bool = True) -> None:
        self._ensure_patched(adapter_name, method)
        self._remaining[(adapter_name, method)] = times
        self._permanent[(adapter_name, method)] = False
        self._patched[(adapter_name, method)].retryable = retryable  # type: ignore[attr-defined]

    def fail_always(self, adapter_name: str, method: str, retryable: bool = False) -> None:
        self._ensure_patched(adapter_name, method)
        self._remaining[(adapter_name, method)] = -1
        self._permanent[(adapter_name, method)] = True
        self._patched[(adapter_name, method)].retryable = retryable  # type: ignore[attr-defined]

    def _ensure_patched(self, adapter_name: str, method: str) -> None:
        key = (adapter_name, method)
        if key in self._patched:
            return
        adapter = ADAPTERS[adapter_name]
        original = getattr(adapter, method)

        def wrapped(*args, **kwargs):
            remaining = self._remaining.get(key, 0)
            if remaining == -1 or remaining > 0:
                if remaining > 0:
                    self._remaining[key] = remaining - 1
                raise RetryableAdapterError(f"injected failure in {adapter_name}.{method}")
            return original(*args, **kwargs)

        wrapped.retryable = True  # type: ignore[attr-defined]
        self._original[key] = original
        self._patched[key] = wrapped
        setattr(adapter, method, wrapped)

    def reset(self) -> None:
        for (adapter_name, method), original in self._original.items():
            setattr(ADAPTERS[adapter_name], method, original)
        self._remaining.clear()
        self._permanent.clear()
        self._original.clear()
        self._patched.clear()
