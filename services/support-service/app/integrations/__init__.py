"""Integration adapters for the support service.

Default adapters are deterministic in-memory fakes (consistent with the repo
convention that real adapters are declared, not yet wired). Real thin HTTP
clients live in this package and are registered explicitly when a deployment
sets the corresponding base-URL env vars. The support service never touches
another service's database; every side effect goes through an adapter."""
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
# Real thin HTTP clients (declared; registered when wired).
from . import (  # noqa: F401
    aaa_client,
    bss_client,
    crm_client,
    ipam_client,
    network_client,
    nms_client,
    notifications,
    oss_client,
    workforce_client,
)
# Deterministic fakes win by default so tests and local dev are hermetic.
from . import fakes  # noqa: F401
from .fakes import STATE, reset_state  # noqa: F401
