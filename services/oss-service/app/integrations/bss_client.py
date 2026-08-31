"""BSS integration adapter (plan validity, payment/billing state)."""
from __future__ import annotations

from .base import Adapter, ValidationResult, ok_result, register

VALID_PLANS = {"plan-fiber-100", "plan-fiber-200", "plan-fiber-500", "plan-fiber-1000"}
PAYMENT_PENDING_CUSTOMERS = {"cust-pay-pending"}
BALANCE_BLOCKED = {"cust-balance-blocked"}


@register
class BssClient(Adapter):
    name = "bss"

    def validate_plan(self, plan_reference) -> ValidationResult:
        if plan_reference in VALID_PLANS:
            return ValidationResult(ok=True, checks={"plan_valid": True})
        return ValidationResult(ok=False, errors=["invalid or inactive plan"], checks={"plan_valid": False})

    def check_payment_eligibility(self, customer_id, billing_account_reference=None) -> ValidationResult:
        blocked = customer_id in PAYMENT_PENDING_CUSTOMERS or customer_id in BALANCE_BLOCKED
        return ValidationResult(
            ok=not blocked,
            errors=[] if not blocked else ["payment pending or account balance blocked"],
            checks={"payment_ok": not blocked},
        )

    def create_billing_account(self, tenant_id, customer_id, plan_reference) -> dict:
        return {"billing_account_reference": f"bacc-{customer_id}"}

    def suspend_billing(self, tenant_id, billing_account_reference) -> dict:
        return {"billing_account_reference": billing_account_reference, "suspended": True}

    def resume_billing(self, tenant_id, billing_account_reference) -> dict:
        return {"billing_account_reference": billing_account_reference, "resumed": True}

    def close_billing_account(self, tenant_id, billing_account_reference) -> dict:
        return {"billing_account_reference": billing_account_reference, "closed": True}

    def update_plan(self, tenant_id, billing_account_reference, plan_reference) -> dict:
        return {"billing_account_reference": billing_account_reference, "plan_reference": plan_reference, "updated": True}
