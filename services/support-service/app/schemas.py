"""API schemas for the support service. Domain rules stay in services/domain;
these models only shape request/response contracts."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SUPPORT_API_PREFIX = "/api/support"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
class TicketCreate(StrictModel):
    tenant_id: UUID | None = Field(default=None, description="Required for management/API creation; portal derives it from the token")
    ticket_type: str = Field(..., description="One of the configured ticket types")
    subject: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=3)
    customer_id: str | None = None
    customer_number: str | None = None
    customer_name: str | None = None
    customer_tier: str | None = None
    service_subscription_id: str | None = None
    subscriber_username: str | None = None
    service_location_id: str | None = None
    billing_account_id: str | None = None
    franchise_id: str | None = None
    reseller_id: str | None = None
    branch_id: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    source_channel: str = "CUSTOMER_PORTAL"
    impact: str = "MEDIUM"
    urgency: str = "MEDIUM"
    priority: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    tags: list[str] | None = None


class CommentIn(StrictModel):
    body: str = Field(..., min_length=1)
    channel: str = "CUSTOMER_PORTAL"
    provider_message_id: str | None = None
    correlation_id: str | None = None
    recipient_reference: str | None = None


class InternalNoteIn(StrictModel):
    body: str = Field(..., min_length=1)
    correlation_id: str | None = None


class AssignIn(StrictModel):
    agent_id: str = Field(..., min_length=1)
    agent_name: str | None = None
    reason: str | None = None
    correlation_id: str | None = None


class TransferIn(StrictModel):
    queue_code: str = Field(..., min_length=1)
    reason: str | None = None
    correlation_id: str | None = None


class ResolveIn(StrictModel):
    resolution_code: str
    summary: str = Field(..., min_length=3)
    customer_explanation: str | None = None
    root_cause_reference: str | None = None
    related_article_id: UUID | None = None
    correlation_id: str | None = None


class ReopenIn(StrictModel):
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class CancelIn(StrictModel):
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class DuplicateIn(StrictModel):
    original_ticket_id: UUID
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class PriorityIn(StrictModel):
    priority: str
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class CategoryIn(StrictModel):
    category_code: str
    subcategory_code: str | None = None
    reason: str | None = None
    correlation_id: str | None = None


class EscalateIn(StrictModel):
    reason: str = Field(..., min_length=3)
    trigger: str = "CUSTOMER_ESCALATION"
    correlation_id: str | None = None


class WatcherIn(StrictModel):
    watcher_type: str = "AGENT"
    watcher_id: str


class LinkIncidentIn(StrictModel):
    incident_id: str
    incident_number: str | None = None
    correlation_id: str | None = None


class LinkOrderIn(StrictModel):
    order_id: str
    order_number: str | None = None
    correlation_id: str | None = None


class LinkJobIn(StrictModel):
    job_id: str
    job_number: str | None = None
    correlation_id: str | None = None


class LinkDisputeIn(StrictModel):
    dispute_id: str
    correlation_id: str | None = None


class RelatedIn(StrictModel):
    relation_type: str = "LINKED"
    to_ticket_id: UUID


class SLAOverrideIn(StrictModel):
    response_deadline: datetime
    resolution_deadline: datetime
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# SLA policies
# ---------------------------------------------------------------------------
class SLAPolicyCreate(StrictModel):
    code: str
    name: str


class SLATargetIn(StrictModel):
    priority: str = "ALL"
    kind: str = "RESOLUTION"
    business_seconds: int = Field(..., gt=0)


class SLAPolicyVersionCreate(StrictModel):
    definition: dict
    targets: list[SLATargetIn]
    activate: bool = False


class ActivateVersionIn(StrictModel):
    version: int


# ---------------------------------------------------------------------------
# Diagnostics / actions
# ---------------------------------------------------------------------------
class ActionRequestIn(StrictModel):
    action_type: str
    payload: dict = {}
    idempotency_key: str | None = None
    correlation_id: str | None = None


class ApproveIn(StrictModel):
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class CancelActionIn(StrictModel):
    reason: str = ""
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------
class CSATIn(StrictModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None
    channel: str = "CUSTOMER_PORTAL"


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------
class ArticleCreate(StrictModel):
    slug: str
    title: str
    body: str
    category_code: str | None = None
    visibility: str = "INTERNAL"
    status: str = "DRAFT"
    tags: list[str] = []


class ArticleUpdate(StrictModel):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Queue / team / routing / agent
# ---------------------------------------------------------------------------
class AgentIn(StrictModel):
    team_code: str
    agent_id: str
    name: str | None = None
    role: str = "AGENT"
    skills: list[str] = []
    locations: list[str] = []


class RoutingRuleIn(StrictModel):
    name: str
    target_queue_code: str
    ticket_type: str | None = None
    category_code: str | None = None
    strategy: str = "ROUND_ROBIN"
    fallback_queue_code: str | None = None
    required_skills: list[str] = []
    priority: int = 100


class InboundMessageIn(StrictModel):
    """Inbound email/WhatsApp/webhook message for threading."""
    tenant_id: UUID
    ticket_id: UUID | None = None
    ticket_number: str | None = None
    reply_token: str | None = None
    provider_message_id: str
    channel: str = "EMAIL"
    direction: str = "INBOUND"
    sender_id: str | None = None
    sender_email: str | None = None
    body: str
    correlation_id: str | None = None
