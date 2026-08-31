"""Pydantic schemas for the CRM API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import (ADDRESS_TYPES, CAF_STATUSES, CONTACT_ROLES, CUSTOMER_LIFECYCLE, CUSTOMER_TYPES, FOLLOWUP_STATUSES, INTERACTION_CHANNELS, KYC_DOCUMENT_TYPES, KYC_STATUSES, KYC_TYPES, LEAD_PRIORITIES, LEAD_SOURCES, LEAD_STAGES, LEAD_TYPES, RISK_LEVELS, RISK_SOURCES)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

class LeadCreate(StrictModel):
    lead_type: Literal["INDIVIDUAL", "BUSINESS"] = "INDIVIDUAL"
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    company_name: str | None = Field(default=None, max_length=255)
    primary_mobile: str = Field(min_length=7, max_length=20)
    alternate_mobile: str | None = Field(default=None, max_length=20)
    primary_email: str | None = Field(default=None, max_length=255)
    alternate_email: str | None = Field(default=None, max_length=255)
    preferred_channel: str | None = Field(default=None, max_length=32)
    requested_service: str | None = Field(default=None, max_length=64)
    requested_plan_reference: str | None = Field(default=None, max_length=128)
    expected_monthly_value: Decimal | None = None
    installation_address_draft: dict[str, Any] = Field(default_factory=dict)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    lead_source: Literal["WALK_IN", "PHONE", "WEBSITE", "MOBILE_APP", "WHATSAPP", "EMAIL", "SOCIAL_MEDIA", "REFERRAL", "FRANCHISE", "FIELD_SALES", "CAMPAIGN", "IMPORT", "API", "CHATBOT", "OTHER"] = "OTHER"
    campaign_reference: str | None = Field(default=None, max_length=128)
    referrer: str | None = Field(default=None, max_length=255)
    franchise_id: UUID | None = None
    branch_id: UUID | None = None
    area: str | None = Field(default=None, max_length=128)
    assigned_salesperson_id: str | None = Field(default=None, max_length=64)
    assigned_team_id: str | None = Field(default=None, max_length=64)
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    sla_deadline: datetime | None = None


class LeadAssignIn(StrictModel):
    assigned_to: str | None = Field(default=None, max_length=64)
    method: str = "MANUAL"
    reason: str | None = Field(default=None, max_length=500)


class LeadTransitionIn(StrictModel):
    to_stage: Literal["NEW", "ASSIGNED", "CONTACTED", "QUALIFICATION", "FEASIBILITY_PENDING", "FEASIBLE", "NOT_FEASIBLE", "PROPOSAL_SENT", "NEGOTIATION", "KYC_PENDING", "WON", "LOST", "DISQUALIFIED", "DUPLICATE", "CONVERTED"]
    reason: str | None = Field(default=None, max_length=500)


class LeadQualifyIn(StrictModel):
    score: int = Field(ge=0, le=100)


class LeadFeasibilityIn(StrictModel):
    feasible: bool
    external_ref: str | None = Field(default=None, max_length=128)


class LeadConvertIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    customer_code: str | None = Field(default=None, max_length=64)
    caf_number: str | None = Field(default=None, max_length=128)
    request_bss: bool = True
    request_oss: bool = True


class InteractionIn(StrictModel):
    channel: Literal["PHONE_CALL", "EMAIL", "SMS", "WHATSAPP", "MEETING", "FIELD_VISIT", "NOTE", "DOCUMENT_REQUEST", "FOLLOW_UP", "SYSTEM_EVENT"]
    direction: Literal["INBOUND", "OUTBOUND"] = "INBOUND"
    subject: str | None = Field(default=None, max_length=255)
    safe_summary: str | None = Field(default=None, max_length=4000)
    outcome: str | None = Field(default=None, max_length=255)
    next_action: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None
    status: str = "COMPLETED"
    external_communication_id: str | None = Field(default=None, max_length=128)


class FollowUpCreate(StrictModel):
    subject: str | None = Field(default=None, max_length=255)
    safe_summary: str | None = Field(default=None, max_length=4000)
    scheduled_at: datetime
    assigned_to: str | None = Field(default=None, max_length=64)


class FollowUpReschedule(StrictModel):
    scheduled_at: datetime


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

class CustomerCreate(StrictModel):
    customer_code: str | None = Field(default=None, max_length=64)
    caf_number: str | None = Field(default=None, max_length=128)
    customer_type: Literal["INDIVIDUAL", "BUSINESS", "GOVERNMENT", "INSTITUTION", "RESELLER_CUSTOMER", "OTHER"] = "INDIVIDUAL"
    legal_name: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    company_trading_name: str | None = Field(default=None, max_length=255)
    father_or_guardian_name: str | None = Field(default=None, max_length=255)
    date_of_birth: date | None = None
    incorporation_date: date | None = None
    gstin: str | None = Field(default=None, max_length=32)
    pan_reference: str | None = Field(default=None, max_length=32)
    phone: str = Field(min_length=7, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    primary_language: str | None = Field(default=None, max_length=32)
    preferred_channel: str | None = Field(default=None, max_length=32)
    acquisition_source: str | None = Field(default=None, max_length=64)
    franchise_id: UUID | None = None
    branch_id: UUID | None = None
    area: str | None = Field(default=None, max_length=128)
    account_manager_id: str | None = Field(default=None, max_length=64)


class CustomerUpdate(StrictModel):
    caf_number: str | None = None
    legal_name: str | None = None
    full_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    company_trading_name: str | None = None
    father_or_guardian_name: str | None = None
    gstin: str | None = None
    phone: str | None = None
    email: str | None = None
    primary_language: str | None = None
    preferred_channel: str | None = None
    area: str | None = None
    account_manager_id: str | None = None


class ContactCreate(StrictModel):
    role: Literal["CONTACT_PERSON", "AUTHORIZED_REPRESENTATIVE", "TECHNICAL", "BILLING", "EMERGENCY"] = "CONTACT_PERSON"
    contact_person_name: str | None = Field(default=None, max_length=255)
    mobile: str | None = Field(default=None, max_length=20)
    alternate_mobile: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    alternate_email: str | None = Field(default=None, max_length=255)
    landline: str | None = Field(default=None, max_length=32)
    whatsapp: str | None = Field(default=None, max_length=20)
    is_primary: bool = False
    communication_preference: dict[str, Any] = Field(default_factory=dict)
    consent_state: str = "NOT_PROVIDED"
    source: str | None = Field(default=None, max_length=32)


class ContactUpdate(StrictModel):
    role: Literal["CONTACT_PERSON", "AUTHORIZED_REPRESENTATIVE", "TECHNICAL", "BILLING", "EMERGENCY"] | None = None
    contact_person_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    is_primary: bool | None = None
    consent_state: str | None = None


class AddressCreate(StrictModel):
    address_type: Literal["BILLING", "INSTALLATION", "REGISTERED_OFFICE", "CORRESPONDENCE", "PERMANENT", "OTHER"]
    country: str | None = Field(default=None, max_length=64)
    state: str | None = Field(default=None, max_length=128)
    district: str | None = Field(default=None, max_length=128)
    city: str | None = Field(default=None, max_length=128)
    zipcode: str | None = Field(default=None, max_length=16)
    door_number: str | None = Field(default=None, max_length=128)
    street: str | None = Field(default=None, max_length=255)
    area: str | None = Field(default=None, max_length=255)
    colony: str | None = Field(default=None, max_length=255)
    building: str | None = Field(default=None, max_length=255)
    landmark: str | None = Field(default=None, max_length=255)
    house_type: str | None = Field(default=None, max_length=64)
    formatted_address: str | None = Field(default=None, max_length=4000)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    geolocation_accuracy: Decimal | None = None
    verification_state: str = "UNVERIFIED"
    valid_from: date | None = None
    valid_to: date | None = None


class ServiceLocationCreate(StrictModel):
    address_id: UUID | None = None
    alias: str | None = Field(default=None, max_length=128)
    status: str = "PLANNED"


class LifecycleTransitionIn(StrictModel):
    to_state: Literal["PROSPECT", "ONBOARDING", "KYC_PENDING", "KYC_REJECTED", "KYC_VERIFIED", "READY_FOR_SERVICE", "ACTIVATION_PENDING", "ACTIVE", "SUSPENSION_PENDING", "SUSPENDED", "REACTIVATION_PENDING", "TERMINATION_PENDING", "TERMINATED", "CLOSED"]
    trigger: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=500)
    related_external_type: str | None = Field(default=None, max_length=64)
    related_external_id: str | None = Field(default=None, max_length=128)


class RiskRecordIn(StrictModel):
    level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    source: Literal["BSS_PAYMENT", "BSS_DUES", "BSS_PAYMENT_FAILURE", "AAA_AUTH_ANOMALY", "AAA_ACCOUNT_SHARING", "NMS_SERVICE_QUALITY", "SUPPORT_COMPLAINTS", "KYC_PROBLEM", "SIEM_SECURITY", "CHURN_BEHAVIOUR", "MANUAL_REVIEW"]
    reason: str = Field(min_length=1, max_length=500)
    source_event_id: str | None = Field(default=None, max_length=128)


class RiskOverrideIn(StrictModel):
    level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason: str = Field(min_length=1, max_length=500)
    expires_in_seconds: int = Field(default=0, ge=0, le=31536000)


class MergeIn(StrictModel):
    duplicate_id: UUID


# ---------------------------------------------------------------------------
# KYC and CAF
# ---------------------------------------------------------------------------

class KycCreateIn(StrictModel):
    kyc_type: Literal["INDIVIDUAL", "BUSINESS", "GOVERNMENT"] = "INDIVIDUAL"


class KycDecisionIn(StrictModel):
    reason: str | None = Field(default=None, max_length=500)
    method: str | None = Field(default=None, max_length=64)


class KycDocumentIn(StrictModel):
    document_type: Literal["AADHAAR", "PAN", "PASSPORT", "DRIVING_LICENSE", "VOTER_ID", "ADDRESS_PROOF", "GST_REGISTRATION", "INCORPORATION", "CAF", "PHOTOGRAPH", "OTHER"]
    storage_reference: str = Field(min_length=1, max_length=2000)
    masked_identifier: str | None = Field(default=None, max_length=64)
    content_type: str | None = Field(default=None, max_length=128)
    size_bytes: int = Field(default=0, ge=0)
    checksum: str | None = Field(default=None, max_length=64)


class CafCreateIn(StrictModel):
    lead_id: UUID | None = None
    lead_source: str | None = Field(default=None, max_length=32)
    application_date: date | None = None
    franchise_id: UUID | None = None
    branch_id: UUID | None = None
    requested_services: list[str] = Field(default_factory=list)
    declaration_accepted: bool = False
    document_checklist: dict[str, Any] = Field(default_factory=dict)


class CafDecisionIn(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class TenantIn(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    policy: dict[str, Any] = Field(default_factory=dict)


class FranchiseIn(StrictModel):
    franchise_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class BranchIn(StrictModel):
    franchise_id: UUID
    branch_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class ExternalReferenceIn(StrictModel):
    service_name: str = Field(min_length=1, max_length=32)
    external_type: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=128)
    external_status: str | None = Field(default=None, max_length=64)
    safe_projection: dict[str, Any] = Field(default_factory=dict)


class FollowUpCompleteIn(StrictModel):
    outcome: str | None = Field(default=None, max_length=255)
