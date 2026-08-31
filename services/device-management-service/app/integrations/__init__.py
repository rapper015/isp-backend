"""Integration adapters. The fake adapters are registered last so deterministic
fakes win over any real HTTP clients when both are importable."""
from .base import (  # noqa: F401
    ActionResult,
    Adapter,
    AdapterError,
    NonRetryableAdapterError,
    RetryableAdapterError,
    StepResult,
    fail_result,
    get_adapter,
    ok_result,
    register,
    reset_adapters,
)
from . import acs  # noqa: F401
from .fakes import (  # noqa: F401  (deterministic fakes win)
    CRMAdapter,
    InventoryAdapter,
    NMSAdapter,
    OSSAdapter,
    STATE,
    SupportAdapter,
    WorkforceAdapter,
    reset_state,
)
