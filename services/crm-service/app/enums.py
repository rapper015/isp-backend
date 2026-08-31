"""CRM bounded-context enums. Canonical values for lead pipeline, lifecycle,
KYC, CAF, addresses, risk and timeline. Keep enums here, not duplicated in
views or models.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Lead pipeline
# ---------------------------------------------------------------------------

LEAD_SOURCES = (
    "WALK_IN", "PHONE", "WEBSITE", "MOBILE_APP", "WHATSAPP", "EMAIL",
    "SOCIAL_MEDIA", "REFERRAL", "FRANCHISE", "FIELD_SALES", "CAMPAIGN",
    "IMPORT", "API", "CHATBOT", "OTHER",
)

# Canonical pipeline stages; tenants may map to their own pipeline labels but
# reporting uses these canonical categories.
LEAD_STAGES = (
    "NEW", "ASSIGNED", "CONTACTED", "QUALIFICATION", "FEASIBILITY_PENDING",
    "FEASIBLE", "NOT_FEASIBLE", "PROPOSAL_SENT", "NEGOTIATION", "KYC_PENDING",
    "WON", "LOST", "DISQUALIFIED", "DUPLICATE", "CONVERTED",
)

LEAD_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")

LEAD_TYPES = ("INDIVIDUAL", "BUSINESS")

FEASIBILITY_STATES = ("UNKNOWN", "PENDING", "FEASIBLE", "NOT_FEASIBLE", "FAILED")

ASSIGNMENT_METHODS = ("MANUAL", "ROUND_ROBIN", "BRANCH", "FRANCHISE", "AREA", "WORKLOAD", "SKILL", "SERVICE")

# ---------------------------------------------------------------------------
# Interactions and follow-ups
# ---------------------------------------------------------------------------

INTERACTION_CHANNELS = (
    "PHONE_CALL", "EMAIL", "SMS", "WHATSAPP", "MEETING", "FIELD_VISIT",
    "NOTE", "DOCUMENT_REQUEST", "FOLLOW_UP", "SYSTEM_EVENT",
)

INTERACTION_DIRECTIONS = ("INBOUND", "OUTBOUND")

FOLLOWUP_STATUSES = ("PENDING", "IN_PROGRESS", "COMPLETED", "MISSED", "CANCELLED", "RESCHEDULED")

COMMUNICATION_CHANNELS = ("PHONE", "EMAIL", "SMS", "WHATSAPP", "PORTAL", "MAIL")

# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

CUSTOMER_TYPES = ("INDIVIDUAL", "BUSINESS", "GOVERNMENT", "INSTITUTION", "RESELLER_CUSTOMER", "OTHER")

ADDRESS_TYPES = ("BILLING", "INSTALLATION", "REGISTERED_OFFICE", "CORRESPONDENCE", "PERMANENT", "OTHER")

ADDRESS_VERIFICATION = ("UNVERIFIED", "VERIFIED", "FAILED")

CONTACT_VERIFICATION = ("UNVERIFIED", "VERIFIED", "FAILED", "EXPIRED")

CONTACT_ROLES = ("CONTACT_PERSON", "AUTHORIZED_REPRESENTATIVE", "TECHNICAL", "BILLING", "EMERGENCY")

OWNERSHIP_ROLES = ("ACCOUNT_MANAGER", "SALES_AGENT", "SALES_MANAGER", "BRANCH_MANAGER", "FRANCHISE_ADMIN", "FRANCHISE_AGENT", "OTHER")

# ---------------------------------------------------------------------------
# CAF
# ---------------------------------------------------------------------------

CAF_STATUSES = (
    "DRAFT", "SUBMITTED", "INCOMPLETE", "UNDER_REVIEW", "VERIFIED",
    "APPROVED", "REJECTED", "CANCELLED", "SUPERSEDED",
)

# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------

KYC_STATUSES = (
    "NOT_STARTED", "DRAFT", "SUBMITTED", "UNDER_REVIEW",
    "ADDITIONAL_INFO_REQUIRED", "VERIFIED", "REJECTED", "EXPIRED", "REVOKED",
)

KYC_TYPES = ("INDIVIDUAL", "BUSINESS", "GOVERNMENT")

KYC_DOCUMENT_TYPES = (
    "AADHAAR", "PAN", "PASSPORT", "DRIVING_LICENSE", "VOTER_ID", "ADDRESS_PROOF",
    "GST_REGISTRATION", "INCORPORATION", "CAF", "PHOTOGRAPH", "OTHER",
)

KYC_DOCUMENT_STATUSES = ("PENDING", "VERIFIED", "REJECTED", "EXPIRED")

# ---------------------------------------------------------------------------
# Lifecycle and risk
# ---------------------------------------------------------------------------

CUSTOMER_LIFECYCLE = (
    "PROSPECT", "ONBOARDING", "KYC_PENDING", "KYC_REJECTED", "KYC_VERIFIED",
    "READY_FOR_SERVICE", "ACTIVATION_PENDING", "ACTIVE", "SUSPENSION_PENDING",
    "SUSPENDED", "REACTIVATION_PENDING", "TERMINATION_PENDING", "TERMINATED",
    "CLOSED",
)

RISK_LEVELS = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")

RISK_SOURCES = (
    "BSS_PAYMENT", "BSS_DUES", "BSS_PAYMENT_FAILURE", "AAA_AUTH_ANOMALY",
    "AAA_ACCOUNT_SHARING", "NMS_SERVICE_QUALITY", "SUPPORT_COMPLAINTS",
    "KYC_PROBLEM", "SIEM_SECURITY", "CHURN_BEHAVIOUR", "MANUAL_REVIEW",
)

TIMELINE_CATEGORIES = (
    "LEAD", "INTERACTION", "FOLLOW_UP", "KYC", "CAF", "CUSTOMER", "ADDRESS",
    "LIFECYCLE", "SERVICE", "BILLING", "AAA", "SUPPORT", "WORKFORCE", "RISK", "SYSTEM",
)
