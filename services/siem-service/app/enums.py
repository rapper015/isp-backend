"""SIEM enums: severity, event categories, case lifecycle, compliance."""

from enum import Enum


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class EventCategory(str, Enum):
    AUTH = "AUTH"
    ACCESS = "ACCESS"
    NETWORK = "NETWORK"
    DATA = "DATA"
    MALWARE = "MALWARE"
    FRAUD = "FRAUD"
    COMPLIANCE = "COMPLIANCE"
    RUNTIME = "RUNTIME"
    VULNERABILITY = "VULNERABILITY"
    OTHER = "OTHER"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    CONTAINED = "CONTAINED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class CaseTransition(str, Enum):
    START_INVESTIGATION = "START_INVESTIGATION"
    CONTAIN = "CONTAIN"
    RESOLVE = "RESOLVE"
    CLOSE = "CLOSE"
    REOPEN = "REOPEN"


CASE_FLOW = {
    CaseStatus.OPEN: {CaseTransition.START_INVESTIGATION: CaseStatus.INVESTIGATING},
    CaseStatus.INVESTIGATING: {CaseTransition.CONTAIN: CaseStatus.CONTAINED},
    CaseStatus.CONTAINED: {CaseTransition.RESOLVE: CaseStatus.RESOLVED},
    CaseStatus.RESOLVED: {CaseTransition.CLOSE: CaseStatus.CLOSED},
    CaseStatus.CLOSED: {CaseTransition.REOPEN: CaseStatus.REOPENED},
    CaseStatus.REOPENED: {CaseTransition.CONTAIN: CaseStatus.CONTAINED},
}

SEVERITY_WEIGHT = {
    Severity.CRITICAL: 100,
    Severity.HIGH: 75,
    Severity.MEDIUM: 45,
    Severity.LOW: 20,
    Severity.INFO: 5,
}


class ViolationStatus(str, Enum):
    OPEN = "OPEN"
    ACKED = "ACKED"
    RESOLVED = "RESOLVED"


class RetentionAction(str, Enum):
    ARCHIVE = "ARCHIVE"
    PURGE = "PURGE"


class DataClass(str, Enum):
    SECURITY_EVENT = "SECURITY_EVENT"
    AUDIT_LOG = "AUDIT_LOG"
    CONSENT = "CONSENT"
    CASE = "CASE"
    IPDR = "IPDR"
    SUBSCRIBER_PII = "SUBSCRIBER_PII"
    SESSION = "SESSION"


class ConsentStatus(str, Enum):
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class RequestType(str, Enum):
    ACCESS = "ACCESS"
    ERASURE = "ERASURE"
    PORTABILITY = "PORTABILITY"


class DataRequestStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    FULFILLED = "FULFILLED"
    REJECTED = "REJECTED"


class LIStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VulnStatus(str, Enum):
    OPEN = "OPEN"
    REMEDIATED = "REMEDIATED"
    ACCEPTED = "ACCEPTED"
