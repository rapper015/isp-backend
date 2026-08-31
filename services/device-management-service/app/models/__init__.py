"""Device-management models — import all tables so metadata is complete."""
from .base import Base, Timestamped, UuidPk  # noqa: F401
from .messaging import AuditLog, InboxMessage, OutboxEvent, Tenant  # noqa: F401
from .catalog import (  # noqa: F401
    DeviceCapability,
    DeviceDataModel,
    DeviceManufacturer,
    DeviceModel,
    DeviceModelVariant,
    ParameterDefinition,
    ParameterMapping,
    SupportedAction,
    SupportedDiagnostic,
    VendorQuirk,
)
from .acs import (  # noqa: F401
    ACSCapability,
    ACSDeviceBinding,
    ACSHealth,
    ACSInstance,
    ACSInstanceCredential,
)
from .identity import (  # noqa: F401
    CpeCapabilitySnapshot,
    CpeEvent,
    CpeOnboarding,
    CpeOwnershipHistory,
    CpeRelationship,
    CpeSecretReference,
    CpeTelemetry,
    ManagedCpe,
)
from .profiles import (  # noqa: F401
    ConfigurationDrift,
    ConfigurationJob,
    ConfigurationStep,
    ConfigurationVerification,
    DeviceConfigurationProfile,
    DeviceConfigurationProfileVersion,
    DeviceConfigurationSnapshot,
    DeviceDesiredState,
    DeviceObservedState,
    ProfileAssignmentDecision,
    ProfileAssignmentRule,
    ProfileParameter,
)
from .diagnostics import DiagnosticJob, DiagnosticResult  # noqa: F401
from .actions import DeviceAction, DeviceActionEvent  # noqa: F401
from .firmware import (  # noqa: F401
    FirmwareApproval,
    FirmwareArtifact,
    FirmwareCohort,
    FirmwareCompatibility,
    FirmwareDeployment,
    FirmwareException,
    FirmwareRollout,
    FirmwareRolloutStage,
    FirmwareVerification,
)
