"""Integration adapters for the workforce service.

Default adapters are deterministic in-memory fakes (consistent with the repo
convention). The workforce service never touches another service's database;
every side effect goes through an adapter."""
from .base import (  # noqa: F401
    ADAPTERS,
    Adapter,
    AdapterError,
    ActionResult,
    NonRetryableAdapterError,
    RetryableAdapterError,
    StepResult,
    get_adapter,
    ok_result,
    fail_result,
    register,
    reset_adapters,
)
# Deterministic fakes win by default so tests and local dev are hermetic.
from . import fakes  # noqa: F401
from .fakes import STATE, reset_state  # noqa: F401
