"""Deterministic fake adapters for the workforce service tests.

Each adapter returns stable data and records calls so tests can assert on
cross-service behaviour. Failure modes are controllable through FakeState
(inventory shortage, activation failure, etc.) without a live dependency."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .base import ActionResult, StepResult, ok_result, register


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeState:
    """Shared mutable state used by every fake adapter; reset() mutates in place."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.customer = {
            "customer_id": "CUST-0001",
            "customer_number": "CUST-0001",
            "customer_name": "Test Customer",
            "tier": "STANDARD",
            "phone": "+919999999999",
            "email": "cust@example.com",
            "service_location": {"id": "loc-1", "lat": 28.6139, "lng": 77.2090, "address": "1 Main Rd"},
        }
        self.subscriber = {
            "subscription_id": "SUB-0001",
            "username": "subs-0001",
            "plan": "plan-fiber-100",
            "activation_state": "PENDING",
            "suspension_state": "NONE",
            "assigned_nas": "nas-1",
            "assigned_ip": None,
        }
        self.oss_order = {
            "order_number": "ORD-0001",
            "order_type": "NEW_CONNECTION",
            "state": "FIELD_INSTALLATION_PENDING",
        }
        self.support_ticket = {"ticket_number": "TKT-2026-00000001", "status": "PENDING_FIELD_VISIT"}
        self.inventory = {
            "stock": {"ONT-SN-1001": {"status": "AVAILABLE", "type": "ONT"},
                      "ROUTER-1": {"status": "AVAILABLE", "type": "ROUTER"},
                      "FIBER_CONNECTOR": {"status": "AVAILABLE", "type": "CONSUMABLE"},
                      "SPLICE": {"status": "AVAILABLE", "type": "CONSUMABLE"}},
            "reservations": {},
            "installations": {},
            "transactions": [],
        }
        self.network = {"activation_result": "PENDING"}
        self.aaa = {"authenticated": False, "coa_ok": True}
        self.nms = {"diagnostics": {"signal_ok": True, "ont_online": True}}
        self.billing = {"eligible": True, "outstanding": "0.00"}
        self.fail = {"crm": None, "support": None, "oss": None, "inventory": None, "ipam": None,
                     "aaa": None, "network": None, "nms": None, "billing": None, "notifications": None}
        self.notifications: list[dict] = []
        self.overrides: dict = {}

    def set_override(self, adapter: str, key: str, value) -> None:
        self.overrides[(adapter, key)] = value

    def get(self, adapter: str, base: dict, key: str):
        if (adapter, key) in self.overrides:
            return self.overrides[(adapter, key)]
        return base.get(key)

    def should_fail(self, adapter: str) -> tuple[bool, str | None]:
        marker = self.fail.get(adapter)
        return (True, marker) if marker else (False, None)


STATE = FakeState()


def reset_state() -> None:
    STATE.reset()


# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------
@register
class CRMAdapter:
    name = "crm"

    def get_customer(self, customer_id: str) -> StepResult:
        failed, reason = STATE.should_fail("crm")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.customer)


# ---------------------------------------------------------------------------
# Support
# ---------------------------------------------------------------------------
@register
class SupportAdapter:
    name = "support"

    def get_ticket(self, ticket_id: str) -> StepResult:
        failed, reason = STATE.should_fail("support")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.support_ticket)

    def update_ticket(self, ticket_id: str, *, status: str, note: str, actor: str, correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=ticket_id, detail={"status": status})


# ---------------------------------------------------------------------------
# OSS
# ---------------------------------------------------------------------------
@register
class OSSAdapter:
    name = "oss"

    def get_order(self, order_id: str) -> StepResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.oss_order)

    def request_remote_activation(self, *, order_id: str, work_order_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ActionResult(ok=True, reference=f"ACT-{uuid.uuid4().hex[:8].upper()}", detail={"state": "ACTIVATING"})

    def get_activation_result(self, order_id: str) -> StepResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(activation_state=STATE.get("oss", STATE.subscriber, "activation_state"),
                         assigned_ip=STATE.get("oss", STATE.subscriber, "assigned_ip"))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
@register
class InventoryAdapter:
    name = "inventory"

    def reserve(self, *, material_code: str, quantity: int, work_order_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("inventory")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        stock = STATE.get("inventory", STATE.inventory, "stock")
        item = stock.get(material_code)
        if item is None or item.get("status") != "AVAILABLE":
            return ActionResult(ok=False, error_code="INSUFFICIENT_STOCK",
                                error_detail=f"material {material_code} unavailable", retryable=True)
        STATE.inventory["reservations"][material_code] = {"quantity": quantity, "work_order_id": work_order_id}
        STATE.inventory["transactions"].append({"type": "RESERVED", "material": material_code, "quantity": quantity})
        return ActionResult(ok=True, reference=f"RES-{uuid.uuid4().hex[:8].upper()}", detail={"status": "RESERVED"})

    def issue(self, *, material_code: str, quantity: int, technician_id: str, actor: str, correlation_id: str) -> ActionResult:
        STATE.inventory["transactions"].append({"type": "ISSUED", "material": material_code, "quantity": quantity})
        return ActionResult(ok=True, reference=f"ISS-{uuid.uuid4().hex[:8].upper()}", detail={"status": "ISSUED"})

    def install_device(self, *, device_type: str, serial_number: str, mac_address: str | None,
                       service_subscription_id: str, work_order_id: str, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("inventory")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        # Authoritative uniqueness: a device cannot be actively installed twice.
        if serial_number in STATE.inventory["installations"]:
            return ActionResult(ok=False, error_code="DEVICE_ALREADY_INSTALLED",
                                error_detail=f"serial {serial_number} already installed", retryable=False)
        STATE.inventory["installations"][serial_number] = {"service": service_subscription_id, "type": device_type}
        STATE.inventory["transactions"].append({"type": "INSTALLED", "material": serial_number})
        return ActionResult(ok=True, reference=f"DEV-{serial_number}", detail={"status": "INSTALLED"})

    def recover_device(self, *, serial_number: str, work_order_id: str, actor: str, correlation_id: str) -> ActionResult:
        STATE.inventory["installations"].pop(serial_number, None)
        STATE.inventory["transactions"].append({"type": "RECOVERED", "material": serial_number})
        return ActionResult(ok=True, reference=f"REC-{serial_number}", detail={"status": "RECOVERED"})

    def consume(self, *, material_code: str, quantity: int, work_order_id: str, actor: str, correlation_id: str) -> ActionResult:
        STATE.inventory["transactions"].append({"type": "CONSUMED", "material": material_code, "quantity": quantity})
        return ActionResult(ok=True, reference=f"CON-{uuid.uuid4().hex[:8].upper()}", detail={"status": "CONSUMED"})


# ---------------------------------------------------------------------------
# IPAM
# ---------------------------------------------------------------------------
@register
class IPAMAdapter:
    name = "ipam"

    def confirm_ready(self, *, service_location_id: str, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(ready=True)


# ---------------------------------------------------------------------------
# AAA / Network Control
# ---------------------------------------------------------------------------
@register
class AAAClient:
    name = "aaa"

    def verify_subscriber(self, *, username: str, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(authenticated=STATE.aaa.get("authenticated", False))


@register
class NetworkAdapter:
    name = "network"

    def verify_bandwidth(self, *, username: str, expected_kbps: int, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(bandwidth_ok=True, measured_kbps=expected_kbps)

    def run_service_test(self, *, username: str, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(service_ok=True)


# ---------------------------------------------------------------------------
# NMS
# ---------------------------------------------------------------------------
@register
class NMSAdapter:
    name = "nms"

    def get_diagnostics(self, *, service_location_id: str, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(**STATE.get("nms", STATE.nms, "diagnostics"))

    def update_repair(self, *, incident_id: str, status: str, note: str, actor: str, correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=incident_id, detail={"status": status})


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------
@register
class BillingAdapter:
    name = "billing"

    def verify_eligibility(self, *, customer_id: str, work_order_id: str, actor: str, correlation_id: str) -> StepResult:
        return ok_result(eligible=STATE.get("billing", STATE.billing, "eligible"),
                         outstanding=STATE.get("billing", STATE.billing, "outstanding"))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@register
class NotificationsAdapter:
    name = "notifications"

    def send(self, *, channel: str, recipient: str, template: str, variables: dict, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("notifications")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        reference = f"NTF-{uuid.uuid4().hex[:8].upper()}"
        STATE.notifications = getattr(STATE, "notifications", []) or []
        STATE.notifications.append({"reference": reference, "channel": channel, "recipient": recipient,
                                    "template": template, "correlation_id": correlation_id})
        return ActionResult(ok=True, reference=reference, detail={"status": "SENT"})
