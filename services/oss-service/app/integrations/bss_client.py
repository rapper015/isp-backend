"""BSS integration adapter (plan validity, payment/billing state)."""
from __future__ import annotations

from os import getenv
from urllib.parse import quote

import httpx

from .base import Adapter, AdapterError, ValidationResult, ok_result, register

VALID_PLANS = {"plan-fiber-100", "plan-fiber-200", "plan-fiber-500", "plan-fiber-1000"}
PAYMENT_PENDING_CUSTOMERS = {"cust-pay-pending"}
BALANCE_BLOCKED = {"cust-balance-blocked"}


@register
class BssClient(Adapter):
    name = "bss"

    def validate_plan(self, plan_reference) -> ValidationResult:
        if not plan_reference:
            return ValidationResult(ok=False, errors=["a service plan is required"], checks={"plan_valid": False})

        # The explicit fake mode keeps the hermetic OSS test suite independent
        # of a running BSS container. Deployments use the live BSS service.
        if getenv("OSS_BSS_PLAN_VALIDATION_MODE", "live").lower() == "fake":
            if plan_reference in VALID_PLANS:
                return ValidationResult(ok=True, checks={"plan_valid": True, "source": "test-double"})
            return ValidationResult(ok=False, errors=["selected plan is not available"], checks={"plan_valid": False})

        base_url = getenv("OSS_BSS_BASE_URL", "http://bss-service:8000").rstrip("/")
        url = f"{base_url}/plans/{quote(str(plan_reference), safe='')}"
        try:
            response = httpx.get(url, timeout=5.0)
        except httpx.HTTPError as error:
            raise AdapterError("BSS plan validation is temporarily unavailable") from error

        if response.status_code == 404:
            return ValidationResult(ok=False, errors=["selected plan was not found in BSS"], checks={"plan_valid": False})
        if response.status_code >= 400:
            raise AdapterError("BSS plan validation is temporarily unavailable")

        plan = response.json()
        active = str(plan.get("status", "")).lower() == "active"
        return ValidationResult(
            ok=active,
            errors=[] if active else ["selected plan is inactive"],
            checks={
                "plan_valid": active,
                "plan_id": str(plan.get("id", plan_reference)),
                "plan_code": plan.get("plan_code"),
            },
        )

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
