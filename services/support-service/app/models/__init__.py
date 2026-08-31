"""Support service models. Importing this module registers every table on the
shared metadata so Alembic and create_all can see them."""
from .base import Timestamped  # noqa: F401
from .messaging import AuditLog, InboxMessage, OutboxEvent, Tenant  # noqa: F401
from .catalog import (  # noqa: F401
    BusinessCalendar,
    Holiday,
    KnowledgeArticle,
    KnowledgeUsage,
    RoutingRule,
    SLAPolicy,
    SLAPolicyVersion,
    SLATarget,
    SupportAgentMembership,
    SupportTeam,
    TicketCategory,
    TicketQueue,
    TicketSubcategory,
    TicketType,
)
from .ticket import (  # noqa: F401
    CustomerSatisfaction,
    SupportAction,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketDiagnosticSnapshot,
    TicketEscalation,
    TicketEvent,
    TicketNumberSequence,
    TicketRelationship,
    TicketResolution,
    TicketSLA,
    TicketSLAPause,
    TicketTag,
    TicketWatcher,
)
