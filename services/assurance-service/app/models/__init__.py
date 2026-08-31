"""Model registry for the Assurance Service. Registers all tables and marks
tenant-owned models for fail-closed routing."""
from .base import Base, Timestamped, UuidPk  # noqa: F401
from .messaging import AsyncTask, AuditLog, InboxMessage, OutboxEvent  # noqa: F401
from .services import (  # noqa: F401
    ServiceComponent,
    ServiceCustomerImpactRule,
    ServiceDefinition,
    ServiceDependency,
    ServiceOwner,
    ServiceTopology,
)
from .slo import (  # noqa: F401
    MaintenanceException,
    MaintenanceWindow,
    SlIDefinition,
    SlIMeasurement,
    SloDefinition,
    SloVersion,
    SloWindowState,
)
from .alerts import (  # noqa: F401
    Alert,
    AlertDefinition,
    AlertDefinitionTest,
    AlertEvent,
    AlertRoute,
    AlertSilence,
    NotificationDelivery,
)
from .incidents import (  # noqa: F401
    ChangeEvent,
    Incident,
    IncidentAction,
    IncidentAlertLink,
    IncidentCommander,
    IncidentCommunication,
    IncidentCustomerImpact,
    IncidentEvent,
    IncidentResponder,
    IncidentServiceImpact,
    IncidentTicketLink,
    Postmortem,
    PostmortemActionItem,
    RootCauseEvidence,
    RootCauseHypothesis,
)
from .kpis import (  # noqa: F401
    DashboardDefinition,
    KpiDefinition,
    KpiMeasurement,
    KpiTarget,
    MetricRegistry,
    NetworkObservation,
    SyntheticCheck,
    SyntheticResult,
)

from ..routing import tenant_owned

_TENANT_OWNED = (
    SlIMeasurement, SloVersion, SloWindowState, MaintenanceWindow, MaintenanceException,
    Alert, AlertEvent, AlertSilence, NotificationDelivery,
    Incident, IncidentEvent, IncidentAlertLink, IncidentServiceImpact, IncidentCustomerImpact,
    IncidentCommander, IncidentResponder, IncidentCommunication, IncidentAction, IncidentTicketLink,
    Postmortem, PostmortemActionItem, RootCauseHypothesis, RootCauseEvidence, ChangeEvent,
    KpiMeasurement, KpiTarget, SyntheticCheck, SyntheticResult, NetworkObservation, DashboardDefinition,
)
for _model in _TENANT_OWNED:
    tenant_owned(_model)
