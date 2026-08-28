"""CRM domain models. Import the models you need from this package."""
from .tenant import Tenant
from .lead import FollowUp, Lead, LeadAssignment, LeadInteraction, LeadStageHistory
from .customer import (Address, Branch, Contact, Customer, CustomerOwnership, ExternalReference, Franchise, ServiceLocation)
from .kyc import KycCase, KycDocument
from .caf import CafRecord
from .lifecycle import CustomerLifecycleEvent, CustomerRisk, TimelineEntry
from .audit import AuditLog, ConsumerInbox, OutboxEvent

__all__ = [
    "Tenant", "Lead", "LeadAssignment", "LeadInteraction", "FollowUp", "LeadStageHistory",
    "Franchise", "Branch", "Customer", "Contact", "Address", "ServiceLocation",
    "CustomerOwnership", "ExternalReference", "KycCase", "KycDocument", "CafRecord",
    "CustomerLifecycleEvent", "CustomerRisk", "TimelineEntry", "AuditLog",
    "OutboxEvent", "ConsumerInbox",
]
