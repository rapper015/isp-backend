"""Deterministic fake cross-service adapters for tests and development.

Each adapter returns stable data and records calls so tests can assert on
cross-service behaviour. Failure modes are controllable through FakeState
without any live dependency."""
from __future__ import annotations

import uuid

from .base import Adapter, ActionResult, StepResult, ok_result, register


class FakeState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.inventory = {
            "assets": {},           # serial -> asset record
            "reservations": {},
            "installations": {},    # serial -> {service, work_order_id}
            "calls": [],
        }
        self.crm = {"customer": {"customer_id": "CUST-0001", "customer_number": "CUST-0001",
                                 "customer_name": "Test Customer", "service_location": "loc-1"}}
        self.oss = {"order": {"order_number": "ORD-0001", "state": "FIELD_INSTALLATION_PENDING"},
                    "activation_result": "PENDING"}
        self.workforce = {"job": {"job_reference": "WO-0001", "status": "COMPLETED"}}
        self.support = {"ticket": {"ticket_number": "TKT-0001", "status": "PENDING_FIELD_VISIT"}}
        self.nms = {"signals": []}
        self.fail = {"inventory": None, "crm": None, "oss": None, "workforce": None, "support": None, "nms": None}
        self.overrides: dict = {}

    def seed_inventory_asset(self, serial: str, *, asset_id: str | None = None, status: str = "IN_STOCK",
                             model: str = "ONT") -> str:
        asset_id = asset_id or f"inv-{serial}"
        self.inventory["assets"][serial] = {"id": asset_id, "serial": serial, "status": status, "model": model}
        return asset_id

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


@register
class InventoryAdapter(Adapter):
    name = "inventory"

    def find_asset_by_serial(self, *, serial: str) -> StepResult:
        failed, reason = STATE.should_fail("inventory")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        asset = STATE.inventory["assets"].get(serial)
        if asset is None:
            return StepResult(ok=False, error_code="ASSET_NOT_FOUND", error_detail="no inventory asset", retryable=False)
        STATE.inventory["calls"].append(("find_asset", serial))
        return ok_result(**asset)

    def reserve(self, *, serial: str, work_order_id: str | None = None, actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("inventory")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        asset = STATE.inventory["assets"].get(serial)
        if asset is None or asset["status"] not in ("IN_STOCK", "PROCURED"):
            return ActionResult(ok=False, error_code="NOT_RESERVABLE", error_detail="asset cannot be reserved", retryable=False)
        asset["status"] = "RESERVED"
        STATE.inventory["reservations"][serial] = {"work_order_id": work_order_id, "actor": actor}
        return ActionResult(ok=True, reference=asset["id"], detail={"status": "RESERVED"})

    def record_installed(self, *, serial: str, work_order_id: str | None = None, service_subscription_id: str | None = None,
                         actor: str, correlation_id: str) -> ActionResult:
        failed, reason = STATE.should_fail("inventory")
        if failed:
            return ActionResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        asset = STATE.inventory["assets"].get(serial)
        if asset is None:
            asset = STATE.seed_inventory_asset(serial) if hasattr(STATE, "seed_inventory_asset") else None
        STATE.inventory["installations"][serial] = {
            "service": service_subscription_id, "work_order_id": work_order_id, "actor": actor}
        return ActionResult(ok=True, reference=f"inst-{serial}", detail={"status": "INSTALLED"})

    def recover(self, *, serial: str, actor: str, correlation_id: str) -> ActionResult:
        STATE.inventory["installations"].pop(serial, None)
        asset = STATE.inventory["assets"].get(serial)
        if asset:
            asset["status"] = "RECOVERED"
        return ActionResult(ok=True, reference=f"rec-{serial}", detail={"status": "RECOVERED"})


@register
class CRMAdapter(Adapter):
    name = "crm"

    def get_customer(self, customer_id: str) -> StepResult:
        failed, reason = STATE.should_fail("crm")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.crm["customer"])


@register
class OSSAdapter(Adapter):
    name = "oss"

    def get_order(self, order_id: str) -> StepResult:
        failed, reason = STATE.should_fail("oss")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.oss["order"])

    def report_device_provisioning(self, *, order_id: str, result: str, detail: dict, actor: str,
                                   correlation_id: str) -> ActionResult:
        STATE.oss["activation_result"] = result
        return ActionResult(ok=True, reference=order_id, detail={"result": result, **detail})


@register
class WorkforceAdapter(Adapter):
    name = "workforce"

    def get_installation(self, *, work_order_id: str) -> StepResult:
        failed, reason = STATE.should_fail("workforce")
        if failed:
            return StepResult(ok=False, error_code=reason, error_detail=reason, retryable=True)
        return ok_result(**STATE.workforce["job"])


@register
class SupportAdapter(Adapter):
    name = "support"

    def link_diagnostic(self, *, ticket_id: str, diagnostic_job_id: str, summary: dict, actor: str,
                        correlation_id: str) -> ActionResult:
        return ActionResult(ok=True, reference=ticket_id, detail={"diagnostic_job_id": diagnostic_job_id})


@register
class NMSAdapter(Adapter):
    name = "nms"

    def emit_signal(self, *, device_id: str, signal: str, severity: str, detail: dict, actor: str,
                    correlation_id: str) -> ActionResult:
        STATE.nms["signals"].append({"device_id": device_id, "signal": signal, "severity": severity, "detail": detail})
        return ActionResult(ok=True, reference=f"sig-{len(STATE.nms['signals'])}", detail={"signal": signal})
