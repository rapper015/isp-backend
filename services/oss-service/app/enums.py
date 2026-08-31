"""OSS bounded-context enums: order types, order states, resource types,
reservation lifecycle, saga states and step states."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

ORDER_TYPES = (
    "NEW_CONNECTION",
    "PACKAGE_UPGRADE",
    "PACKAGE_DOWNGRADE",
    "SERVICE_RENEWAL",
    "SERVICE_SUSPENSION",
    "SERVICE_REACTIVATION",
    "SERVICE_DISCONNECTION",
    "SERVICE_TERMINATION",
    "SERVICE_RELOCATION",
    "ADDON_ACTIVATION",
    "ADDON_DEACTIVATION",
    "STATIC_IP_ASSIGNMENT",
    "STATIC_IP_RELEASE",
    "DEVICE_REPLACEMENT",
    "DEVICE_PICKUP",
)

ORDER_STATES = (
    "DRAFT",
    "SUBMITTED",
    "VALIDATING",
    "VALIDATION_FAILED",
    "PAYMENT_PENDING",
    "READY_FOR_FULFILMENT",
    "RESOURCE_RESERVATION",
    "FIELD_INSTALLATION_PENDING",
    "PROVISIONING",
    "VERIFYING",
    "COMPLETED",
    "FAILED",
    "COMPENSATING",
    "ROLLED_BACK",
    "CANCELLATION_REQUESTED",
    "CANCELLED",
    "MANUAL_INTERVENTION_REQUIRED",
)

ORDER_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")

ORDER_SOURCES = ("CRM", "PORTAL", "SELF_SERVICE", "FRANCHISE", "CALL_CENTRE", "FIELD", "IMPORT", "API")

ORDER_COMMANDS = ("CREATE", "VALIDATE", "SUBMIT", "APPROVE_PAYMENT", "RESERVE", "PROVISION", "VERIFY", "COMPLETE", "CANCEL", "RETRY", "RESUME", "COMPENSATE")

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

RESOURCE_TYPES = (
    "IPV4", "IPV6_PREFIX", "VLAN", "POP", "NODE", "SWITCH_PORT", "OLT_PORT",
    "PON_PORT", "ONT", "NAS", "CIRCUIT", "STATIC_IP", "ADDRESS_POOL",
    "SERVICE_PROFILE", "DEVICE",
)

RESERVATION_STATES = ("AVAILABLE", "RESERVED", "ALLOCATED", "RELEASING", "RELEASED", "QUARANTINED", "UNAVAILABLE")

# ---------------------------------------------------------------------------
# Sagas
# ---------------------------------------------------------------------------

SAGA_STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "COMPENSATING", "COMPENSATED", "MANUAL_INTERVENTION", "TIMED_OUT", "CANCELLED")

SAGA_STEP_STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "COMPENSATED", "SKIPPED")

WORKFLOW_TYPES = (
    "NEW_CONNECTION",
    "PACKAGE_UPGRADE",
    "PACKAGE_DOWNGRADE",
    "SERVICE_SUSPENSION",
    "SERVICE_REACTIVATION",
    "SERVICE_TERMINATION",
    "SERVICE_RELOCATION",
)

SERVICE_STATES = (
    "PENDING_ACTIVATION",
    "ACTIVE",
    "ACTIVATION_FAILED",
    "SUSPENDED",
    "REACTIVATING",
    "TERMINATING",
    "TERMINATED",
)
