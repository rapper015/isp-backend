"""Workforce service models. Importing this module registers every table on the
shared metadata so Alembic and create_all can see them."""
from .base import Timestamped  # noqa: F401
from .messaging import AuditLog, InboxMessage, OutboxEvent, Tenant  # noqa: F401
from .catalog import (  # noqa: F401
    BusinessCalendar,
    ChecklistItem,
    ChecklistTemplate,
    ChecklistTemplateVersion,
    FieldSLAPolicy,
    FieldSLAPolicyVersion,
    FieldSLATarget,
    Holiday,
    ServiceArea,
    WorkOrderTemplate,
    WorkOrderTemplateVersion,
    WorkOrderType,
)
from .technician import (  # noqa: F401
    TechnicianAvailability,
    TechnicianCertification,
    TechnicianProfile,
    TechnicianShift,
    TechnicianSkill,
    TechnicianStatusLog,
)
from .workorder import (  # noqa: F401
    Appointment,
    ChecklistResponse,
    CustomerAcknowledgement,
    DeviceInstallation,
    DispatchPlan,
    FieldAttachment,
    FieldEscalation,
    FieldSLAInstance,
    FieldSLAPause,
    FieldVisit,
    MaterialRequirement,
    MaterialUsage,
    OfflineCommand,
    ProofOfWork,
    QualityReview,
    TimeEntry,
    VisitCheckIn,
    VisitCheckOut,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderBlocker,
    WorkOrderChecklist,
    WorkOrderEvent,
    WorkOrderNumberSequence,
    WorkOrderRelationship,
    WorkOrderResult,
)
