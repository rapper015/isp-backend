"""Support bounded-context enums.

These are the canonical values used by the state machine, services and API.
Ticket types / categories / queues are tenant-configurable records in the
database; the tuples here are the documented defaults seeded for new tenants.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Ticket types (default catalogue; tenant-configurable)
# ---------------------------------------------------------------------------
TICKET_TYPES = (
    "INCIDENT",
    "COMPLAINT",
    "SERVICE_REQUEST",
    "INQUIRY",
    "BILLING_QUERY",
    "BILLING_DISPUTE",
    "CONNECTIVITY_ISSUE",
    "SPEED_ISSUE",
    "AUTHENTICATION_ISSUE",
    "DEVICE_ISSUE",
    "INSTALLATION_ISSUE",
    "RELOCATION_QUERY",
    "PAYMENT_QUERY",
    "OUTAGE_RELATED",
    "APP_SUPPORT",
    "SECURITY_REPORT",
    "FEEDBACK",
    "OTHER",
)

# Ticket types that always require an OSS order before they can resolve.
SERVICE_ORDER_REQUIRED_TYPES = {"SERVICE_REQUEST", "RELOCATION_QUERY", "INSTALLATION_ISSUE"}

# ---------------------------------------------------------------------------
# Ticket lifecycle (internal state machine)
# ---------------------------------------------------------------------------
TICKET_STATES = (
    "NEW",
    "TRIAGE",
    "ASSIGNED",
    "IN_PROGRESS",
    "PENDING_CUSTOMER",
    "PENDING_INTERNAL_TEAM",
    "PENDING_VENDOR",
    "PENDING_FIELD_VISIT",
    "PENDING_OSS_ORDER",
    "PENDING_BILLING_ACTION",
    "ESCALATED",
    "RESOLVED",
    "CLOSED",
    "REOPENED",
    "CANCELLED",
    "DUPLICATE",
)

TERMINAL_TICKET_STATES = {"CLOSED", "CANCELLED", "DUPLICATE"}
RESOLVED_STATES = {"RESOLVED", "CLOSED"}
OPEN_STATES = set(TICKET_STATES) - TERMINAL_TICKET_STATES

# ---------------------------------------------------------------------------
# Customer-visible status (derived; internal detail is never exposed)
# ---------------------------------------------------------------------------
CUSTOMER_STATUS_MAP = {
    "NEW": "SUBMITTED",
    "TRIAGE": "SUBMITTED",
    "ASSIGNED": "SUBMITTED",
    "IN_PROGRESS": "IN_PROGRESS",
    "PENDING_CUSTOMER": "WAITING_FOR_YOUR_RESPONSE",
    "PENDING_INTERNAL_TEAM": "IN_PROGRESS",
    "PENDING_VENDOR": "IN_PROGRESS",
    "PENDING_FIELD_VISIT": "VISIT_SCHEDULED",
    "PENDING_OSS_ORDER": "IN_PROGRESS",
    "PENDING_BILLING_ACTION": "IN_PROGRESS",
    "ESCALATED": "IN_PROGRESS",
    "RESOLVED": "RESOLVED",
    "CLOSED": "CLOSED",
    "REOPENED": "IN_PROGRESS",
    "CANCELLED": "CANCELLED",
    "DUPLICATE": "CLOSED",
}

CUSTOMER_VISIBLE_STATES = tuple(sorted(set(CUSTOMER_STATUS_MAP.values())))

# ---------------------------------------------------------------------------
# Priority / impact / urgency / severity — distinct concepts
# ---------------------------------------------------------------------------
PRIORITIES = ("P1_CRITICAL", "P2_HIGH", "P3_MEDIUM", "P4_LOW")
IMPACT_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
URGENCY_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SEVERITIES = ("SEV1", "SEV2", "SEV3", "SEV4", "SEV5")

# impact x urgency -> priority (configurable in production; default matrix).
PRIORITY_MATRIX = {
    ("CRITICAL", "CRITICAL"): "P1_CRITICAL",
    ("CRITICAL", "HIGH"): "P1_CRITICAL",
    ("CRITICAL", "MEDIUM"): "P2_HIGH",
    ("CRITICAL", "LOW"): "P2_HIGH",
    ("HIGH", "CRITICAL"): "P1_CRITICAL",
    ("HIGH", "HIGH"): "P2_HIGH",
    ("HIGH", "MEDIUM"): "P2_HIGH",
    ("HIGH", "LOW"): "P3_MEDIUM",
    ("MEDIUM", "CRITICAL"): "P2_HIGH",
    ("MEDIUM", "HIGH"): "P3_MEDIUM",
    ("MEDIUM", "MEDIUM"): "P3_MEDIUM",
    ("MEDIUM", "LOW"): "P4_LOW",
    ("LOW", "CRITICAL"): "P3_MEDIUM",
    ("LOW", "HIGH"): "P3_MEDIUM",
    ("LOW", "MEDIUM"): "P4_LOW",
    ("LOW", "LOW"): "P4_LOW",
}

# ---------------------------------------------------------------------------
# Source channels
# ---------------------------------------------------------------------------
SOURCE_CHANNELS = (
    "CUSTOMER_PORTAL",
    "MOBILE_APP",
    "EMAIL",
    "SMS",
    "WHATSAPP",
    "PHONE",
    "CHATBOT",
    "WALK_IN",
    "FRANCHISE",
    "RESELLER",
    "NMS",
    "SYSTEM",
    "AGENT",
)

# ---------------------------------------------------------------------------
# Communication
# ---------------------------------------------------------------------------
COMMENT_DIRECTIONS = ("INBOUND", "OUTBOUND")
COMMENT_VISIBILITIES = ("PUBLIC", "INTERNAL")
COMMENT_KINDS = ("PUBLIC_REPLY", "CUSTOMER_MESSAGE", "INTERNAL_NOTE", "SYSTEM_EVENT", "AUTOMATED_NOTIFICATION", "DIAGNOSTIC_RESULT", "ACTION_RESULT")
DELIVERY_STATUSES = ("PENDING", "SENT", "DELIVERED", "FAILED", "RETRYING")

# ---------------------------------------------------------------------------
# SLA
# ---------------------------------------------------------------------------
SLA_TARGET_KINDS = (
    "ACKNOWLEDGEMENT",
    "FIRST_HUMAN_RESPONSE",
    "NEXT_RESPONSE",
    "ASSIGNMENT",
    "WORK_START",
    "ONSITE_VISIT",
    "RESOLUTION",
    "CLOSURE",
)
SLA_STATUSES = ("ACTIVE", "PAUSED", "AT_RISK", "BREACHED", "COMPLETED")

# ---------------------------------------------------------------------------
# Assignment / routing
# ---------------------------------------------------------------------------
ROUTING_STRATEGIES = ("ROUND_ROBIN", "LEAST_LOADED", "SKILL_BASED", "LOCATION_BASED", "MANUAL")

# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
ESCALATION_TRIGGERS = (
    "SLA_AT_RISK",
    "SLA_BREACH",
    "NO_ASSIGNMENT",
    "NO_PROGRESS",
    "REPEATED_REOPEN",
    "CUSTOMER_ESCALATION",
    "SEVERITY_P1_P2",
    "VIP_CUSTOMER",
    "SECURITY_IMPACT",
    "FINANCIAL_IMPACT",
    "FAILED_SUPPORT_ACTION",
    "FAILED_WORKFORCE_JOB",
    "FAILED_OSS_ORDER",
    "REPEATED_CATEGORY",
)
ESCALATION_ACTIONS = (
    "NOTIFY_AGENT",
    "NOTIFY_TEAM_LEAD",
    "REASSIGN_QUEUE",
    "ADD_SUPERVISOR_WATCHER",
    "RAISE_PRIORITY",
    "CREATE_MAJOR_INCIDENT_CANDIDATE",
    "CREATE_NOC_INVESTIGATION",
    "CREATE_WORKFORCE_JOB",
    "CREATE_OSS_ORDER",
    "NOTIFY_CUSTOMER",
    "REQUIRE_MANAGEMENT_REVIEW",
)

# ---------------------------------------------------------------------------
# Controlled support actions
# ---------------------------------------------------------------------------
SUPPORT_ACTION_TYPES = (
    "REFRESH_SUBSCRIBER_CONTEXT",
    "RE_RUN_DIAGNOSTICS",
    "REAPPLY_SESSION_POLICY",
    "DISCONNECT_REAUTHORIZE",
    "REQUEST_COA",
    "REQUEST_AAA_RECONCILIATION",
    "NAS_REACHABILITY_CHECK",
    "IP_ASSIGNMENT_RECONCILIATION",
    "RETRY_PROVISIONING_STEP",
    "CREATE_OSS_ORDER",
    "CREATE_WORKFORCE_JOB",
    "REQUEST_BILLING_REVIEW",
    "REQUEST_PAYMENT_RECONCILIATION",
    "LINK_OUTAGE",
    "UNLINK_OUTAGE",
)

# Disruptive actions require preview + confirmation + approval.
DISRUPTIVE_ACTION_TYPES = {
    "DISCONNECT_REAUTHORIZE",
    "REQUEST_COA",
    "REAPPLY_SESSION_POLICY",
    "REQUEST_AAA_RECONCILIATION",
    "IP_ASSIGNMENT_RECONCILIATION",
}

SUPPORT_ACTION_STATUSES = (
    "REQUESTED",
    "AUTHORIZATION_REQUIRED",
    "APPROVED",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "MANUAL_INTERVENTION_REQUIRED",
)

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
DIAGNOSTIC_STATUSES = ("PENDING", "PARTIAL", "COMPLETE", "FAILED")
CHECK_STATUSES = ("PASS", "WARN", "FAIL", "UNKNOWN")

# ---------------------------------------------------------------------------
# Resolution / closure
# ---------------------------------------------------------------------------
RESOLUTION_CODES = (
    "CUSTOMER_EDUCATION",
    "CONFIGURATION_CORRECTED",
    "SESSION_RESET",
    "POLICY_REAPPLIED",
    "NETWORK_FAULT_REPAIRED",
    "DEVICE_REPLACED",
    "BILLING_CORRECTION",
    "PAYMENT_RECONCILED",
    "PROVISIONING_COMPLETED",
    "KNOWN_OUTAGE_RESOLVED",
    "DUPLICATE",
    "UNABLE_TO_REPRODUCE",
    "CUSTOMER_UNAVAILABLE",
    "NO_FAULT_FOUND",
    "WORKAROUND_PROVIDED",
    "CANCELLED_BY_CUSTOMER",
)

# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------
CSAT_RATING_MIN = 1
CSAT_RATING_MAX = 5

# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
MALWARE_STATUSES = ("PENDING", "CLEAN", "INFECTED", "QUARANTINED")
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf",
    "text/plain",
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MiB

# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------
KB_VISIBILITIES = ("PUBLIC", "INTERNAL")
KB_STATUSES = ("DRAFT", "ACTIVE", "ARCHIVED")

# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
RELATION_TYPES = ("PARENT", "CHILD", "LINKED", "DUPLICATE_OF", "RELATED_OUTAGE")
