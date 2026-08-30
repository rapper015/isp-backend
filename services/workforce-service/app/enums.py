"""Workforce enums: work-order lifecycle, technician state, field ops."""
from enum import Enum


class WorkOrderStatus(str, Enum):
    CREATED = "CREATED"
    SCHEDULING = "SCHEDULING"
    ASSIGNED = "ASSIGNED"
    DISPATCHED = "DISPATCHED"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


WO_FLOW = {
    WorkOrderStatus.CREATED: {"SCHEDULING", "CANCELLED"},
    WorkOrderStatus.SCHEDULING: {"ASSIGNED", "CANCELLED"},
    WorkOrderStatus.ASSIGNED: {"DISPATCHED", "SCHEDULING", "REJECTED", "CANCELLED"},
    WorkOrderStatus.DISPATCHED: {"EN_ROUTE", "ARRIVED", "CANCELLED"},
    WorkOrderStatus.EN_ROUTE: {"ARRIVED", "CANCELLED"},
    WorkOrderStatus.ARRIVED: {"IN_PROGRESS", "CANCELLED"},
    WorkOrderStatus.IN_PROGRESS: {"PAUSED", "COMPLETED"},
    WorkOrderStatus.PAUSED: {"IN_PROGRESS", "COMPLETED", "CANCELLED"},
    WorkOrderStatus.COMPLETED: set(),
    WorkOrderStatus.CANCELLED: set(),
    WorkOrderStatus.REJECTED: {"ASSIGNED"},
}


class WorkOrderType(str, Enum):
    INSTALLATION = "INSTALLATION"
    REPAIR = "REPAIR"
    MAINTENANCE = "MAINTENANCE"
    PREVENTIVE_MAINTENANCE = "PREVENTIVE_MAINTENANCE"
    EMERGENCY = "EMERGENCY"
    SURVEY = "SURVEY"
    HANDOVER = "HANDOVER"


class TechnicianStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    OFF_DUTY = "OFF_DUTY"
    ON_LEAVE = "ON_LEAVE"


class AssignmentStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class InventoryStatus(str, Enum):
    IN_STOCK = "IN_STOCK"
    ISSUED = "ISSUED"
    RETURNED = "RETURNED"
    DEFECTIVE = "DEFECTIVE"
    RMA = "RMA"


class VisitType(str, Enum):
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    SITE = "SITE"


class ProofKind(str, Enum):
    PHOTO = "PHOTO"
    SIGNATURE = "SIGNATURE"
    GPS = "GPS"
    DOCUMENT = "DOCUMENT"


class EscalationLevel(str, Enum):
    LEVEL_1 = "LEVEL_1"
    LEVEL_2 = "LEVEL_2"
    LEVEL_3 = "LEVEL_3"


class EscalationStatus(str, Enum):
    OPEN = "OPEN"
    ACKED = "ACKED"
    RESOLVED = "RESOLVED"


class ShiftStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SiteCheckKind(str, Enum):
    SITE_FEASIBILITY = "SITE_FEASIBILITY"
    POWER = "POWER"
    SIGNAL = "SIGNAL"
    ROUTE = "ROUTE"
