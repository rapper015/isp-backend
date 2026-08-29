"""Workforce bounded-context enums.

Appointment state, visit state, technician status, dispatch state and
work-order state are deliberately separate concepts — they are never collapsed
into one generic status field."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Work-order types (configurable; these are the documented defaults)
# ---------------------------------------------------------------------------
WORK_ORDER_TYPES = (
    "NEW_INSTALLATION",
    "SITE_SURVEY",
    "FAULT_REPAIR",
    "PREVENTIVE_MAINTENANCE",
    "CORRECTIVE_MAINTENANCE",
    "SERVICE_RELOCATION",
    "ONT_INSTALLATION",
    "ONT_REPLACEMENT",
    "ROUTER_INSTALLATION",
    "ROUTER_REPLACEMENT",
    "DEVICE_PICKUP",
    "SERVICE_DISCONNECTION",
    "CABLE_REPAIR",
    "FIBER_SPLICING",
    "SIGNAL_TEST",
    "NETWORK_INSPECTION",
    "CUSTOMER_PREMISES_VISIT",
    "OUTAGE_RESTORATION",
    "OTHER",
)

INSTALLATION_TYPES = {"NEW_INSTALLATION", "ONT_INSTALLATION", "ROUTER_INSTALLATION", "SERVICE_RELOCATION"}

# ---------------------------------------------------------------------------
# Work-order lifecycle
# ---------------------------------------------------------------------------
WORK_ORDER_STATES = (
    "DRAFT",
    "CREATED",
    "VALIDATING",
    "READY_FOR_SCHEDULING",
    "SCHEDULED",
    "ASSIGNED",
    "DISPATCHED",
    "EN_ROUTE",
    "ARRIVED",
    "IN_PROGRESS",
    "PAUSED",
    "BLOCKED",
    "CUSTOMER_UNAVAILABLE",
    "AWAITING_PARTS",
    "AWAITING_REMOTE_ACTION",
    "RESCHEDULE_REQUIRED",
    "EXECUTION_COMPLETED",
    "VERIFICATION_PENDING",
    "QA_REJECTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)

TERMINAL_WORK_ORDER_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
OPEN_WORK_ORDER_STATES = set(WORK_ORDER_STATES) - TERMINAL_WORK_ORDER_STATES

# ---------------------------------------------------------------------------
# Appointment lifecycle (separate from work-order state)
# ---------------------------------------------------------------------------
APPOINTMENT_STATES = (
    "PROPOSED",
    "CUSTOMER_CONFIRMATION_PENDING",
    "CONFIRMED",
    "RESCHEDULED",
    "TECHNICIAN_DISPATCHED",
    "TECHNICIAN_ARRIVED",
    "COMPLETED",
    "CUSTOMER_NO_SHOW",
    "TECHNICIAN_NO_SHOW",
    "CANCELLED",
)

# ---------------------------------------------------------------------------
# Field visit lifecycle
# ---------------------------------------------------------------------------
VISIT_STATES = (
    "PLANNED",
    "EN_ROUTE",
    "ON_SITE",
    "IN_PROGRESS",
    "PAUSED",
    "COMPLETED",
    "ABANDONED",
)

# ---------------------------------------------------------------------------
# Technician operational status (separate from work-order status)
# ---------------------------------------------------------------------------
TECHNICIAN_STATUSES = (
    "OFF_SHIFT",
    "AVAILABLE",
    "RESERVED",
    "DISPATCHED",
    "EN_ROUTE",
    "ON_SITE",
    "WORKING",
    "ON_BREAK",
    "UNAVAILABLE",
    "EMERGENCY_UNAVAILABLE",
)

EMPLOYMENT_TYPES = ("EMPLOYEE", "CONTRACTOR", "FREELANCE")

# ---------------------------------------------------------------------------
# Assignment / dispatch
# ---------------------------------------------------------------------------
ASSIGNMENT_STRATEGIES = (
    "MANUAL",
    "ROUND_ROBIN",
    "LEAST_LOADED",
    "SKILL_BASED",
    "CERTIFICATION_BASED",
    "SERVICE_AREA_BASED",
    "PROXIMITY_BASED",
    "SLA_DEADLINE_BASED",
    "PRIORITY_BASED",
    "TEAM_BASED",
    "CONTRACTOR_BASED",
)

DISPATCH_STATES = (
    "UNASSIGNED",
    "QUEUED",
    "ASSIGNED",
    "DISPATCHED",
    "EN_ROUTE",
    "ON_SITE",
    "WORKING",
    "COMPLETED",
    "FAILED",
)

# ---------------------------------------------------------------------------
# Checklists
# ---------------------------------------------------------------------------
CHECKLIST_ITEM_TYPES = (
    "CHECKBOX",
    "TEXT",
    "NUMBER",
    "SELECT",
    "MULTI_SELECT",
    "DATE_TIME",
    "PHOTO",
    "VIDEO",
    "DOCUMENT",
    "SIGNATURE",
    "GPS_CAPTURE",
    "BARCODE_SCAN",
    "SERIAL_NUMBER",
    "MAC_ADDRESS",
    "OPTICAL_READING",
    "SPEED_TEST",
    "YES_NO",
)

CHECKLIST_RULE_TYPES = ("REQUIRED", "CONDITIONAL", "REPEATABLE")

# ---------------------------------------------------------------------------
# Proof of work
# ---------------------------------------------------------------------------
PROOF_TYPES = (
    "CHECKIN_COORDINATES",
    "CHECKOUT_COORDINATES",
    "SERVER_TIMESTAMP",
    "PHOTOGRAPH",
    "VIDEO",
    "SERIAL_NUMBER",
    "MAC_ADDRESS",
    "BARCODE_SCAN",
    "OPTICAL_READING",
    "SIGNAL_READING",
    "SPEED_TEST",
    "INSTALLATION_DIAGRAM",
    "CUSTOMER_ACKNOWLEDGEMENT",
    "SUPERVISOR_ACKNOWLEDGEMENT",
    "TECHNICIAN_NOTES",
    "MATERIAL_USAGE",
    "BEFORE_AFTER_EVIDENCE",
)

PROOF_VERIFICATION_STATES = ("PENDING", "VERIFIED", "REJECTED", "EXCEPTION_APPROVED")

# ---------------------------------------------------------------------------
# Quality review
# ---------------------------------------------------------------------------
QA_STATES = ("NOT_REQUIRED", "PENDING", "UNDER_REVIEW", "APPROVED", "REJECTED", "REWORK_REQUIRED")

# ---------------------------------------------------------------------------
# Field SLA
# ---------------------------------------------------------------------------
FIELD_SLA_TARGET_KINDS = (
    "TIME_TO_VALIDATE",
    "TIME_TO_SCHEDULE",
    "TIME_TO_ASSIGN",
    "TIME_TO_ACCEPT",
    "TIME_TO_DISPATCH",
    "ARRIVAL",
    "TIME_ON_SITE",
    "TIME_TO_RESTORE",
    "TIME_TO_COMPLETE",
    "TIME_TO_VERIFY_QA",
)
FIELD_SLA_STATUSES = ("ACTIVE", "PAUSED", "AT_RISK", "BREACHED", "COMPLETED")

# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
FIELD_ESCALATION_TRIGGERS = (
    "UNASSIGNED",
    "ASSIGNMENT_REJECTED_REPEATEDLY",
    "APPOINTMENT_UNCONFIRMED",
    "TECHNICIAN_LATE",
    "TECHNICIAN_NOT_CHECKED_IN",
    "WORK_NOT_STARTED",
    "SLA_AT_RISK",
    "SLA_BREACH",
    "INVENTORY_UNAVAILABLE",
    "REMOTE_ACTIVATION_FAILED",
    "CUSTOMER_UNAVAILABLE_REPEATEDLY",
    "QA_REJECTED_REPEATEDLY",
    "TECHNICIAN_CONNECTIVITY_LOST",
    "EMERGENCY_JOB_BLOCKED",
    "EVIDENCE_MISSING",
)
FIELD_ESCALATION_ACTIONS = (
    "NOTIFY_TECHNICIAN",
    "NOTIFY_DISPATCHER",
    "NOTIFY_SUPERVISOR",
    "REASSIGN_TECHNICIAN",
    "ADD_TECHNICIAN",
    "RAISE_PRIORITY",
    "CREATE_SUPPORT_UPDATE",
    "ESCALATE_TO_NOC",
    "ESCALATE_TO_OSS",
    "ESCALATE_TO_INVENTORY",
    "NOTIFY_CUSTOMER",
    "REQUIRE_MANUAL_INTERVENTION",
)

# ---------------------------------------------------------------------------
# Inventory / materials
# ---------------------------------------------------------------------------
INVENTORY_TRANSACTION_TYPES = (
    "RESERVED",
    "ISSUED",
    "TRANSFERRED",
    "INSTALLED",
    "CONSUMED",
    "RETURNED",
    "RECOVERED",
    "DAMAGED",
    "LOST",
    "QUARANTINED",
)

MATERIAL_USAGE_TYPES = ("INSTALLED", "CONSUMED", "RETURNED", "DAMAGED", "LOST")

# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------
BLOCKER_TYPES = (
    "CUSTOMER_UNAVAILABLE",
    "ACCESS_DENIED",
    "SAFETY_CONCERN",
    "INVENTORY_UNAVAILABLE",
    "REMOTE_ACTION_PENDING",
    "NETWORK_ISSUE",
    "SITE_ISSUE",
    "EQUIPMENT_FAULTY",
    "WEATHER",
    "OTHER",
)
BLOCKER_STATUSES = ("OPEN", "RESOLVED", "ESCALATED")

# ---------------------------------------------------------------------------
# QA / verification
# ---------------------------------------------------------------------------
CUSTOMER_VERIFICATION_METHODS = (
    "CUSTOMER_OTP",
    "AUTHORIZED_CONTACT_CONFIRMATION",
    "CUSTOMER_SIGNATURE",
    "PHOTOGRAPH_CONSENT",
    "DOCUMENT_REFERENCE",
    "SUPERVISOR_OVERRIDE",
)

# ---------------------------------------------------------------------------
# Offline sync
# ---------------------------------------------------------------------------
OFFLINE_COMMAND_STATUSES = ("RECEIVED", "PROCESSED", "REJECTED", "CONFLICT", "RETRYABLE")

# ---------------------------------------------------------------------------
# Time entries
# ---------------------------------------------------------------------------
TIME_ENTRY_TYPES = ("TRAVEL", "ON_SITE", "WORK", "BREAK", "WAITING", "BLOCKED")

# ---------------------------------------------------------------------------
# Priorities / severity
# ---------------------------------------------------------------------------
PRIORITIES = ("P1_CRITICAL", "P2_HIGH", "P3_MEDIUM", "P4_LOW")
SEVERITIES = ("SEV1", "SEV2", "SEV3", "SEV4", "SEV5")

# ---------------------------------------------------------------------------
# Work-order result codes (repair results are configurable)
# ---------------------------------------------------------------------------
WORK_ORDER_RESULT_CODES = (
    "INSTALLED",
    "REPAIRED",
    "REPLACED",
    "SURVEY_COMPLETED",
    "NO_FAULT_FOUND",
    "UNABLE_TO_ACCESS",
    "CUSTOMER_CANCELLED",
    "OUT_OF_SCOPE",
    "WORKAROUND_PROVIDED",
    "BLOCKED_BY_CUSTOMER",
    "OTHER",
)

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
WORK_ORDER_SOURCE_CHANNELS = ("OSS", "SUPPORT", "NMS", "MANUAL", "BATCH", "API")
