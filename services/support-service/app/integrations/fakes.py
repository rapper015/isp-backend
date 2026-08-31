"""Deterministic fake adapters for the support service tests.

Each adapter returns stable data and records every call so tests can assert on
cross-service behaviour. Failure modes are controllable through FakeState
(outage, auth failures, suspension, unreachable NAS, ...) without a live
dependency on another service."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .base import ActionResult, StepResult, ok_result, register


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeState:
    """Shared mutable state used by every fake adapter.

    reset() reinitializes in place so ``from .fakes import STATE`` references
    keep pointing at the same object across test resets."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.customer = {
            "customer_number": "CUST-0001",
            "customer_name": "Test Customer",
            "tier": "STANDARD",
            "lifecycle_state": "ACTIVE",
            "contact_preference": "EMAIL",
            "service_location": "loc-1",
        }
        self.billing = {
            "billing_status": "CURRENT",
            "outstanding_amount": "0.00",
            "last_payment_at": _now(),
            "financial_restriction": None,
            "invoice_summary": {"current_invoice": "0.00", "period": "2026-08"},
            "currency": "INR",
        }
        self.subscriber = {
            "subscription_id": "SUB-0001",
            "plan": "plan-fiber-100",
            "access_technology": "FTTH",
            "activation_state": "ACTIVE",
            "suspension_state": "NONE",
            "assigned_nas": "nas-1",
            "assigned_ip": "10.1.1.10",
            "calling_station_id": "AA:BB:CC:DD:EE:FF",
            "onu_reference": "ONT-SN-1001",
        }
        self.sessions = {
            "active_sessions": [{"session_id": "s1", "started_at": _now(), "framed_ip": "10.1.1.10"}],
            "last_auth_result": "ACCEPT",
            "auth_failures": [],
            "last_interim": _now(),
            "disconnect_history": [],
            "applied_policy": "policy-100",
        }
        self.policy = {
            "expected_bandwidth": 100_000,
            "applied_bandwidth": 100_000,
            "fup_state": "NONE",
            "policy_version": "v3",
            "recent_coa": [],
            "policy_drift": False,
        }
        self.nms = {
            "nas_health": "UP",
            "pop_health": "UP",
            "olt_health": "UP",
            "onu_health": "UP",
            "recent_alarms": [],
            "known_outage": None,
            "interface_status": "UP",
            "latency_ms": 5,
            "packet_loss_pct": 0.0,
        }
        self.outages: list[dict] = []
        self.provisioning_orders: list[dict] = []
        self.workforce_jobs: list[dict] = []
        self.notifications: list[dict] = []
        self.fail = {"crm": None, "bss": None, "aaa": None, "oss": None, "nms": None, "network": None,
                     "workforce": None, "ipam": None, "notifications": None}
        self.overrides: dict = {}

    def set_override(self, adapter: str, key_path: str, value) -> None:
        self.overrides[(adapter, key_path)] = value

    def get(self, adapter: str, base: dict, key: str):
        if (adapter, key) in self.overrides:
            return self.overrides[(adapter, key)]
        return base.get(key)

    def should_fail(self, adapter: str) -> tuple[bool, str | None]:
        marker = self.fail.get(adapter)
        if marker:
            return True, marker
        return False, None


STATE = FakeState()


def reset_state() -> None:
    """Reinitialize the shared fake state in place so imported references to
    ``STATE`` keep pointing at the same object."""
    STATE.reset()


# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------
@register
class CRMAdapter:
    name = "crm"

    def get_customer_context(self, customer_id: str) -> StepResult:
        failed, reason = STATE.should_fail("crm")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"crm unavailable: {reason}", retryable=True)
        base = STATE.customer
        return ok_result(
            customer_id=customer_id,
            customer_number=STATE.get("crm", base, "customer_number"),
            customer_name=STATE.get("crm", base, "customer_name"),
            tier=STATE.get("crm", base, "tier"),
            lifecycle_state=STATE.get("crm", base, "lifecycle_state"),
            contact_preference=STATE.get("crm", base, "contact_preference"),
            service_location=STATE.get("crm", base, "service_location"),
        )


# ---------------------------------------------------------------------------
# BSS
# ---------------------------------------------------------------------------
@register
class BSSAdapter:
    name = "bss"

    def get_billing_context(self, billing_account_id: str | None, customer_id: str | None, include_payment_detail: bool = False) -> StepResult:
        failed, reason = STATE.should_fail("bss")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"bss unavailable: {reason}", retryable=True)
        base = STATE.billing
        payload = {
            "billing_account_id": billing_account_id,
            "customer_id": customer_id,
            "billing_status": STATE.get("bss", base, "billing_status"),
            "outstanding_amount": STATE.get("bss", base, "outstanding_amount"),
            "last_payment_at": STATE.get("bss", base, "last_payment_at"),
            "financial_restriction": STATE.get("bss", base, "financial_restriction"),
            "invoice_summary": STATE.get("bss", base, "invoice_summary"),
            "currency": STATE.get("bss", base, "currency"),
        }
        # Full payment details are only surfaced to explicitly authorized callers.
        if include_payment_detail:
            payload["payment_detail"] = {"masked_card": "XXXX-XXXX-XXXX-1234", "method": "card"}
        return ok_result(**payload)

    def request_billing_review(self, ticket_id: str, *, actor: str, correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=f"BRV-{uuid.uuid4().hex[:8].upper()}", detail={"status": "REVIEW_OPENED"})

    def reconcile_payment(self, ticket_id: str, *, actor: str, correlation_id: str, amount: str | None = None) -> ActionResult:
        failed, reason = STATE.should_fail("bss")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"RCN-{uuid.uuid4().hex[:8].upper()}", detail={"status": "RECONCILED"})


# ---------------------------------------------------------------------------
# AAA (sessions, auth diagnostics, CoA, disconnect)
# ---------------------------------------------------------------------------
@register
class AAAAdapter:
    name = "aaa"

    def get_session_context(self, subscriber_username: str | None, calling_station_id: str | None) -> StepResult:
        failed, reason = STATE.should_fail("aaa")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"aaa unavailable: {reason}", retryable=True)
        base = STATE.sessions
        return ok_result(
            subscriber_username=subscriber_username,
            calling_station_id=calling_station_id,
            active_sessions=STATE.get("aaa", base, "active_sessions"),
            last_auth_result=STATE.get("aaa", base, "last_auth_result"),
            auth_failures=STATE.get("aaa", base, "auth_failures"),
            last_interim=STATE.get("aaa", base, "last_interim"),
            disconnect_history=STATE.get("aaa", base, "disconnect_history"),
            applied_policy=STATE.get("aaa", base, "applied_policy"),
        )

    def disconnect_and_reauth(self, *, subscriber_username: str, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("aaa")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"DSC-{uuid.uuid4().hex[:8].upper()}", detail={"session": "disconnected"})

    def request_coa(self, *, subscriber_username: str, attributes: dict | None, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("aaa")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"COA-{uuid.uuid4().hex[:8].upper()}", detail={"coa": "accepted"})

    def request_reconciliation(self, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=f"AAA-REC-{uuid.uuid4().hex[:8].upper()}", detail={"status": "QUEUED"})

    def nas_reachability(self, nas_reference: str, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("aaa")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        health = STATE.get("nms", STATE.nms, "nas_health")
        return ActionResult(ok=health == "UP", reference=f"NAS-{nas_reference}", detail={"health": health})


# ---------------------------------------------------------------------------
# OSS (subscriber context + service orders)
# ---------------------------------------------------------------------------
@register
class OSSAdapter:
    name = "oss"

    def get_subscriber_context(self, subscription_id: str | None, subscriber_username: str | None) -> StepResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"oss unavailable: {reason}", retryable=True)
        base = STATE.subscriber
        return ok_result(
            subscription_id=subscription_id or STATE.get("oss", base, "subscription_id"),
            plan=STATE.get("oss", base, "plan"),
            access_technology=STATE.get("oss", base, "access_technology"),
            activation_state=STATE.get("oss", base, "activation_state"),
            suspension_state=STATE.get("oss", base, "suspension_state"),
            assigned_nas=STATE.get("oss", base, "assigned_nas"),
            assigned_ip=STATE.get("oss", base, "assigned_ip"),
            calling_station_id=STATE.get("oss", base, "calling_station_id"),
            onu_reference=STATE.get("oss", base, "onu_reference"),
            recent_orders=STATE.provisioning_orders,
        )

    def create_order(self, *, tenant_id: str, order_type: str, customer_id: str | None, subscription_id: str | None,
                     service_location_id: str | None, requested_snapshot: dict | None, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        order = {
            "order_number": f"ORD-{uuid.uuid4().hex[:8].upper()}",
            "order_type": order_type,
            "state": "DRAFT",
        }
        STATE.provisioning_orders.append(order)
        return ActionResult(ok=True, reference=order["order_number"], detail={"order_type": order_type, "state": "DRAFT"})

    def retry_order_step(self, order_reference: str, step: str | None, *, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=order_reference, detail={"step": step, "status": "RETRIED"})


# ---------------------------------------------------------------------------
# Network control (policy reapplication) — owned by aaa/network-control
# ---------------------------------------------------------------------------
@register
class NetworkAdapter:
    name = "network"

    def get_policy_context(self, subscriber_username: str | None, subscription_id: str | None) -> StepResult:
        failed, reason = STATE.should_fail("network")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"network unavailable: {reason}", retryable=True)
        base = STATE.policy
        return ok_result(
            subscriber_username=subscriber_username,
            subscription_id=subscription_id,
            expected_bandwidth=STATE.get("network", base, "expected_bandwidth"),
            applied_bandwidth=STATE.get("network", base, "applied_bandwidth"),
            fup_state=STATE.get("network", base, "fup_state"),
            policy_version=STATE.get("network", base, "policy_version"),
            recent_coa=STATE.get("network", base, "recent_coa"),
            policy_drift=STATE.get("network", base, "policy_drift"),
        )

    def reapply_policy(self, *, subscriber_username: str, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("network")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"POL-{uuid.uuid4().hex[:8].upper()}", detail={"status": "REAPPLIED"})


# ---------------------------------------------------------------------------
# NMS (device/outage context)
# ---------------------------------------------------------------------------
@register
class NMSAdapter:
    name = "nms"

    def get_device_context(self, nas_reference: str | None, pop: str | None, service_location_id: str | None) -> StepResult:
        failed, reason = STATE.should_fail("nms")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=f"nms unavailable: {reason}", retryable=True)
        base = STATE.nms
        return ok_result(
            nas_reference=nas_reference,
            pop=pop,
            service_location_id=service_location_id,
            nas_health=STATE.get("nms", base, "nas_health"),
            pop_health=STATE.get("nms", base, "pop_health"),
            olt_health=STATE.get("nms", base, "olt_health"),
            onu_health=STATE.get("nms", base, "onu_health"),
            recent_alarms=STATE.get("nms", base, "recent_alarms"),
            known_outage=STATE.get("nms", base, "known_outage"),
            interface_status=STATE.get("nms", base, "interface_status"),
            latency_ms=STATE.get("nms", base, "latency_ms"),
            packet_loss_pct=STATE.get("nms", base, "packet_loss_pct"),
        )

    def list_active_outages(self, tenant_id: str | None) -> StepResult:
        return ok_result(outages=list(STATE.outages))

    def create_noc_investigation(self, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=f"NOC-{uuid.uuid4().hex[:8].upper()}", detail={"status": "INVESTIGATING"})


# ---------------------------------------------------------------------------
# Workforce (field jobs)
# ---------------------------------------------------------------------------
@register
class WorkforceAdapter:
    name = "workforce"

    def create_job(self, *, tenant_id: str, job_type: str, ticket_id: str, service_location_id: str | None,
                   requested_at: str | None, required_skill: str | None, notes: str | None, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("workforce")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        job = {
            "job_number": f"JOB-{uuid.uuid4().hex[:8].upper()}",
            "job_type": job_type,
            "status": "SCHEDULED",
        }
        STATE.workforce_jobs.append(job)
        return ActionResult(ok=True, reference=job["job_number"], detail={"status": "SCHEDULED"})


# ---------------------------------------------------------------------------
# IPAM (IP assignment reconciliation)
# ---------------------------------------------------------------------------
@register
class IPAMAdapter:
    name = "ipam"

    def reconcile_assignment(self, *, subscription_id: str | None, expected_ip: str | None, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("ipam")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"IPAM-{uuid.uuid4().hex[:8].upper()}", detail={"status": "RECONCILED"})


# ---------------------------------------------------------------------------
# Notifications (email/sms/push delivery — owned by the notification service)
# ---------------------------------------------------------------------------
@register
class NotificationsAdapter:
    name = "notifications"

    def send(self, *, channel: str, recipient: str, template: str, variables: dict, ticket_id: str | None, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("notifications")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        reference = f"NTF-{uuid.uuid4().hex[:8].upper()}"
        STATE.notifications.append({
            "reference": reference,
            "channel": channel,
            "recipient": recipient,
            "template": template,
            "ticket_id": str(ticket_id) if ticket_id else None,
            "correlation_id": correlation_id,
        })
        return ActionResult(ok=True, reference=reference, detail={"status": "SENT"})
