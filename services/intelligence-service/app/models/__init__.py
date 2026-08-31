"""Model registry for the Intelligence Service. Registers all tables and marks
tenant-owned models for fail-closed routing."""
from .base import Base, Timestamped, UuidPk  # noqa: F401
from .messaging import AsyncTask, AuditLog, InboxMessage, OutboxEvent  # noqa: F401
from .contracts import (  # noqa: F401
    AnalyticalRecord, ConsentRecord, DataContract, DataQualityCheck, DatasetSnapshot,
    LineageLink, PipelineRun, RawEvent,
)
from .features import FeatureDefinition, FeatureValue, OnlineFeatureValue  # noqa: F401
from .mlops import (  # noqa: F401
    MlModel, ModelCard, ModelDeployment, ModelMonitor, TrainingRun,
)
from .aiops import (  # noqa: F401
    CapacityForecast, ChurnScore, FailurePrediction, FraudActionRecommendation, FraudCase,
    FraudDecision, FraudEvidence, FraudRule, FraudSignal, KillSwitch, Recommendation,
    RemediationApproval, RemediationIntent, RemediationOutcome, RemediationPolicy,
    RemediationStep, RetentionCandidate,
)
from .operations import (  # noqa: F401
    AutomationCoverage, Bottleneck, NodeProfit, PersonalizationProfile,
    RegionProfitability,
)
from .aiops_advanced import (  # noqa: F401
    BusinessTwin, NetworkTwin, PricingChange, ScalingAction, SentimentResponse,
    UpsellSuggestion, VoiceInteraction, WorkforceTask,
)

from ..routing import tenant_owned

_TENANT_OWNED = (
    RawEvent, AnalyticalRecord, DatasetSnapshot, DataQualityCheck, PipelineRun, LineageLink,
    ConsentRecord, FeatureValue, OnlineFeatureValue,
    TrainingRun, MlModel, ModelCard, ModelDeployment, ModelMonitor,
    FraudRule, FraudSignal, FraudCase, FraudEvidence, FraudDecision, FraudActionRecommendation,
    ChurnScore, RetentionCandidate, FailurePrediction, CapacityForecast,
    Recommendation, RemediationIntent, RemediationApproval, RemediationStep, RemediationOutcome,
    KillSwitch,
    PersonalizationProfile, Bottleneck, AutomationCoverage, NodeProfit, RegionProfitability,
    NetworkTwin, ScalingAction, PricingChange, BusinessTwin, UpsellSuggestion,
    VoiceInteraction, SentimentResponse, WorkforceTask,
)
for _model in _TENANT_OWNED:
    tenant_owned(_model)
