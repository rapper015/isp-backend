"""CRM integration adapter (customer identity, lifecycle, KYC, CAF, location).

Fake passes any well-formed customer id; special marker ids (used in tests)
trigger specific validation failures: kyc-pending, caf-pending, location-missing,
blocked."""
from __future__ import annotations

from .base import Adapter, ValidationResult, ok_result, register

KYC_PENDING_IDS = {"cust-kyc-pending"}
CAF_PENDING_IDS = {"cust-caf-pending"}
BLOCKED_IDS = {"cust-blocked"}
LOCATION_MISSING = {"loc-missing"}


@register
class CrmClient(Adapter):
    name = "crm"

    def validate_customer(self, tenant_id, customer_id, service_location_id, order_type=None) -> ValidationResult:
        checks = {
            "customer_exists": True,
            "not_blocked": customer_id not in BLOCKED_IDS,
            "kyc_approved": customer_id not in KYC_PENDING_IDS and customer_id not in BLOCKED_IDS,
            "caf_signed": customer_id not in CAF_PENDING_IDS and customer_id not in BLOCKED_IDS,
            "location_valid": bool(service_location_id) and service_location_id not in LOCATION_MISSING,
        }
        errors = [name.replace("_", " ") for name, value in checks.items() if not value]
        return ValidationResult(ok=not errors, checks=checks, errors=errors)

    def get_customer(self, tenant_id, customer_id) -> dict:
        return {
            "customer_id": customer_id,
            "name": f"Customer {customer_id}",
            "lifecycle": "TERMINATED" if customer_id in BLOCKED_IDS else "ACTIVE",
        }

    def get_location(self, tenant_id, service_location_id) -> dict:
        return {"service_location_id": service_location_id, "verified": service_location_id != LOCATION_MISSING}

    def update_customer_notes(self, tenant_id, customer_id, note: str):
        return ok_result({"customer_id": customer_id})
