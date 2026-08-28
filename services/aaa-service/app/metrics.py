"""Small dependency-free, low-cardinality process metrics for AAA operations."""
from collections import Counter
from threading import Lock

_values: Counter[str] = Counter()
_lock = Lock()


def increment(name: str) -> None:
    with _lock:
        _values[name] += 1


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_values)
