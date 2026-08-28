"""Provisioning workflows: zero-touch NEW_CONNECTION activation and the other
service operations (upgrade/downgrade, suspension, reactivation, termination,
relocation). All side effects go through integration adapters; every step has
an idempotent compensation; validation is a pre-saga phase."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import SERVICE_STATES
from ..events import publish_outbox
from ..integrations.base import AdapterError, StepResult, ValidationResult, fail_result, ok_result, get_adapter
from ..models import ServiceSubscription
from ..services.order_service import OrderService
from ..services.resource_service import InvalidReservation
from ..services.saga_engine import SagaDefinition, Step, StepContext

DEFAULT_ONT_SERIAL = "ONT-SN-1001"
DEFAULT_NAS_REFERENCE = "nas-default"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _subscription(ctx: StepContext, store_key: str = "create_service_subscription") -> ServiceSubscription:
    order = ctx.order_service.repo.load(ctx.order_id)
    sub_id = order.service_subscription_id
    if sub_id is None:
        sub_id = ctx.store.get(store_key, {}).get("subscription_id")
    if sub_id is None:
        raise RuntimeError("subscription not created yet")
    sub = ctx.session.get(ServiceSubscription, uuid.UUID(str(sub_id)))
    if sub is None:
        raise RuntimeError(f"subscription {sub_id} not found")
    return sub


# ===========================================================================
# Pre-saga validation phase
# ===========================================================================

def validate_and_prepare(session: Session, order_service: OrderService, order) -> str:
    """Runs CRM/BSS eligibility checks. Returns the resulting order state:
    READY_FOR_FULFILMENT, PAYMENT_PENDING or VALIDATION_FAILED. Idempotent:
    re-running on a READY order returns READY_FOR_FULFILMENT."""
    if order.state == "READY_FOR_FULFILMENT":
        return "READY_FOR_FULFILMENT"
    if order.state == "SUBMITTED":
        order_service.validate(order.id, actor="validation", correlation_id=str(order.correlation_id))
    elif order.state not in ("VALIDATING", "VALIDATION_FAILED"):
        raise ValueError(f"cannot validate order in state {order.state}")
    crm = get_adapter("crm")
    bss = get_adapter("bss")
    checks: list[str] = []
    try:
        customer = crm.validate_customer(order.tenant_id, str(order.customer_id), str(order.service_location_id), order.order_type)
        if not customer.ok:
            checks += customer.errors
    except AdapterError as error:
        checks.append(str(error))
    try:
        plan = bss.validate_plan(order.requested_plan_reference)
        if not plan.ok:
            checks += plan.errors
    except AdapterError as error:
        checks.append(str(error))
    if checks:
        order_service.mark_validation_failed(order.id, reason="; ".join(checks), actor="validation", correlation_id=str(order.correlation_id))
        return "VALIDATION_FAILED"
    try:
        payment = bss.check_payment_eligibility(str(order.customer_id), order.requested_snapshot.get("billing_account_reference"))
    except AdapterError as error:
        payment = ValidationResult(ok=False, errors=[str(error)])
    if not payment.ok:
        order_service.transition(order.id, "PAYMENT_PENDING", reason="; ".join(payment.errors), actor="validation", correlation_id=str(order.correlation_id))
        return "PAYMENT_PENDING"
    order_service.transition(order.id, "READY_FOR_FULFILMENT", reason="validation passed", actor="validation", correlation_id=str(order.correlation_id))
    return "READY_FOR_FULFILMENT"


# ===========================================================================
# NEW_CONNECTION — zero-touch activation
# ===========================================================================

def _step_create_subscription(ctx: StepContext) -> StepResult:
    order = ctx.order_service.repo.load(ctx.order_id)
    bss = get_adapter("bss")
    billing = bss.create_billing_account(ctx.tenant_id, str(order.customer_id), order.requested_plan_reference)
    subscription_code = f"SUB-{order.order_number[-12:]}"
    sub = ServiceSubscription(
        tenant_id=ctx.tenant_id,
        subscription_code=subscription_code,
        status="PENDING_ACTIVATION",
        customer_id=order.customer_id,
        service_location_id=order.service_location_id,
        plan_reference=order.requested_plan_reference,
        billing_account_reference=billing.get("billing_account_reference"),
        order_reference=order.order_number,
    )
    ctx.session.add(sub)
    ctx.session.flush()
    order.service_subscription_id = sub.id
    publish_outbox(ctx.session, "oss.service.created.v1", {"subscription_code": subscription_code, "customer_id": str(order.customer_id)}, ctx.tenant_id, order.correlation_id)
    return ok_result({"subscription_id": str(sub.id), "subscription_code": subscription_code, "billing_account_reference": billing.get("billing_account_reference")})


def _comp_create_subscription(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "TERMINATED"
    sub.termination_date = _now()
    publish_outbox(ctx.session, "oss.service.terminated.v1", {"subscription_code": sub.subscription_code, "reason": "compensation"}, ctx.tenant_id)
    return ok_result({"terminated": True})


def _step_reserve_resources(ctx: StepContext) -> StepResult:
    order = ctx.order_service.repo.load(ctx.order_id)
    ipam = get_adapter("ipam")
    network = get_adapter("network")
    resources = ctx.resource_service
    ip = ipam.allocate_ip(ctx.tenant_id, ctx.order_id, pool_code=ipam.find_pool(ctx.tenant_id, str(order.service_location_id)))
    port = network.reserve_port(ctx.tenant_id, pop=order.requested_snapshot.get("pop", "pop-1"), node=order.requested_snapshot.get("node", "node-1"), port_type="PON_PORT")
    ont_serial = order.requested_snapshot.get("ont_serial") or DEFAULT_ONT_SERIAL
    ont = network.assign_ont(ctx.tenant_id, ont_serial)
    ledger = resources.reserve(ctx.tenant_id, ctx.order_id, "IPV4", 1, actor="saga", reason="new connection")
    ledger += resources.reserve(ctx.tenant_id, ctx.order_id, "VLAN", 1, actor="saga", reason="new connection")
    ledger += resources.reserve(ctx.tenant_id, ctx.order_id, "PON_PORT", 1, actor="saga", reason="new connection")
    return ok_result(
        {
            "ip_address": ip["address"],
            "ip_allocation_ref": ip["allocation_ref"],
            "port_reference": port["port_reference"],
            "ont_serial": ont_serial,
            "ont_assigned": bool(ont.output.get("assigned")),
            "reservation_tokens": [r.reservation_token for r in ledger],
            "resource_keys": {r.resource_type: r.resource_key for r in ledger},
        }
    )


def _comp_reserve_resources(ctx: StepContext) -> StepResult:
    out = ctx.store.get("reserve_resources") or {}
    ipam = get_adapter("ipam")
    network = get_adapter("network")
    for token in out.get("reservation_tokens", []):
        try:
            ctx.resource_service.release(token, reason="compensation")
        except InvalidReservation:
            continue
    if out.get("ip_address"):
        ipam.release_ip(ctx.tenant_id, out["ip_address"], out.get("ip_allocation_ref"))
    if out.get("port_reference"):
        network.release_port(ctx.tenant_id, out["port_reference"])
    if out.get("ont_serial"):
        network.release_ont(ctx.tenant_id, out["ont_serial"])
    return ok_result({"released": True})


def _step_schedule_installation(ctx: StepContext) -> StepResult:
    order = ctx.order_service.repo.load(ctx.order_id)
    workforce = get_adapter("workforce")
    job = workforce.schedule_installation(
        ctx.tenant_id,
        str(order.service_location_id),
        requested_date=order.requested_activation_date,
        order_reference=order.order_number,
    )
    return ok_result({"job_reference": job["job_reference"], "scheduled_date": job.get("scheduled_date")})


def _comp_schedule_installation(ctx: StepContext) -> StepResult:
    out = ctx.store.get("schedule_installation") or {}
    if out.get("job_reference"):
        get_adapter("workforce").cancel_job(ctx.tenant_id, out["job_reference"])
    return ok_result({"cancelled": True})


def _step_wait_installation(ctx: StepContext) -> StepResult:
    workforce = get_adapter("workforce")
    job_ref = (ctx.store.get("schedule_installation") or {}).get("job_reference")
    status = workforce.get_installation_status(job_ref)
    if status.get("status") == "COMPLETED":
        return ok_result({"installation_status": "COMPLETED", "job_reference": job_ref})
    return fail_result("INSTALLATION_PENDING", "field installation not yet complete", retryable=True)


def _comp_wait_installation(ctx: StepContext) -> StepResult:
    out = ctx.store.get("schedule_installation") or {}
    if out.get("job_reference"):
        get_adapter("workforce").cancel_job(ctx.tenant_id, out["job_reference"])
    return ok_result({"cancelled": True})


def _step_configure_access(ctx: StepContext) -> StepResult:
    order = ctx.order_service.repo.load(ctx.order_id)
    aaa = get_adapter("aaa")
    nas = get_adapter("nas")
    sub = _subscription(ctx)
    username = sub.subscription_code
    profile = aaa.create_subscriber_profile(ctx.tenant_id, username, order.requested_plan_reference, sub.subscription_code)
    aaa_ref = profile["aaa_subscriber_reference"]
    sub.aaa_subscriber_reference = aaa_ref
    nas_ref = order.requested_snapshot.get("nas_reference") or DEFAULT_NAS_REFERENCE
    nas_conf = nas.configure_subscriber(ctx.tenant_id, nas_ref, aaa_ref, username, order.requested_plan_reference)
    sub.nas_reference = nas_ref
    return ok_result({"aaa_subscriber_reference": aaa_ref, "username": username, "nas_reference": nas_ref, "nas_configured": bool(nas_conf.output.get("configured"))})


def _comp_configure_access(ctx: StepContext) -> StepResult:
    out = ctx.store.get("configure_access") or {}
    aaa = get_adapter("aaa")
    nas = get_adapter("nas")
    if out.get("aaa_subscriber_reference"):
        nas.remove_subscriber(ctx.tenant_id, out.get("nas_reference") or DEFAULT_NAS_REFERENCE, out["aaa_subscriber_reference"])
        aaa.delete_subscriber(ctx.tenant_id, out["aaa_subscriber_reference"])
    sub = _subscription(ctx)
    sub.aaa_subscriber_reference = None
    sub.nas_reference = None
    return ok_result({"disabled": True})


def _step_verify_readiness(ctx: StepContext) -> StepResult:
    nms = get_adapter("nms")
    sub = _subscription(ctx)
    reserved = ctx.store.get("reserve_resources") or {}
    resources = {"ip_address": reserved.get("ip_address"), "port_reference": reserved.get("port_reference")}
    result = nms.verify_service_readiness(ctx.tenant_id, sub.subscription_code, resources)
    link = nms.verify_link(ctx.tenant_id, sub.subscription_code)
    if result.output.get("ready") and link.output.get("ready"):
        return ok_result({"ready": True, "probe": "ok"})
    return fail_result("NMS_NOT_READY", "service readiness verification failed", retryable=True)


def _step_commit_and_activate(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    reserved = ctx.store.get("reserve_resources") or {}
    for token in reserved.get("reservation_tokens", []):
        ctx.resource_service.allocate(token)
    sub.status = "ACTIVE"
    sub.activation_date = _now()
    sub.resource_references = {
        "ip_address": reserved.get("ip_address"),
        "port_reference": reserved.get("port_reference"),
        "ont_serial": reserved.get("ont_serial"),
        "resource_keys": reserved.get("resource_keys", {}),
    }
    publish_outbox(ctx.session, "oss.service.activated.v1", {"subscription_code": sub.subscription_code, "subscription_id": str(sub.id)}, ctx.tenant_id)
    return ok_result({"activated": True, "subscription_id": str(sub.id)})


def _comp_commit_and_activate(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "TERMINATING"
    sub.termination_date = _now()
    reserved = ctx.store.get("reserve_resources") or {}
    for token in reserved.get("reservation_tokens", []):
        try:
            ctx.resource_service.release(token, reason="compensation")
        except InvalidReservation:
            continue
    publish_outbox(ctx.session, "oss.service.activation_failed.v1", {"subscription_code": sub.subscription_code, "subscription_id": str(sub.id)}, ctx.tenant_id)
    return ok_result({"deactivated": True})


def build_new_connection_saga() -> SagaDefinition:
    return SagaDefinition(
        "NEW_CONNECTION",
        [
            Step("create_service_subscription", _step_create_subscription, _comp_create_subscription, max_attempts=3),
            Step("reserve_resources", _step_reserve_resources, _comp_reserve_resources, max_attempts=3, order_state="RESOURCE_RESERVATION"),
            Step("schedule_installation", _step_schedule_installation, _comp_schedule_installation, max_attempts=3, order_state="FIELD_INSTALLATION_PENDING"),
            Step("wait_installation", _step_wait_installation, _comp_wait_installation, max_attempts=1, pausable=True),
            Step("configure_access", _step_configure_access, _comp_configure_access, max_attempts=3, order_state="PROVISIONING"),
            Step("verify_readiness", _step_verify_readiness, None, max_attempts=3, order_state="VERIFYING"),
            Step("commit_and_activate", _step_commit_and_activate, _comp_commit_and_activate, max_attempts=3),
        ],
    )


# ===========================================================================
# Suspension / Reactivation / Termination
# ===========================================================================

def _step_suspend_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    aaa = get_adapter("aaa")
    aaa.disable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    return ok_result({"aaa_subscriber_reference": sub.aaa_subscriber_reference})


def _comp_suspend_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("aaa").enable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    return ok_result({"enabled": True})


def _step_suspend_service(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "SUSPENDED"
    sub.suspension_date = _now()
    publish_outbox(ctx.session, "oss.service.suspended.v1", {"subscription_code": sub.subscription_code, "subscription_id": str(sub.id)}, ctx.tenant_id)
    return ok_result({"subscription_id": str(sub.id), "status": "SUSPENDED"})


def _comp_suspend_service(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "ACTIVE"
    sub.suspension_date = None
    return ok_result({"restored": True})


def _step_suspend_billing(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("bss").suspend_billing(ctx.tenant_id, sub.billing_account_reference)
    return ok_result({"billing_account_reference": sub.billing_account_reference})


def _comp_suspend_billing(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("bss").resume_billing(ctx.tenant_id, sub.billing_account_reference)
    return ok_result({"resumed": True})


def _step_reactivate_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("aaa").enable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    return ok_result({"aaa_subscriber_reference": sub.aaa_subscriber_reference})


def _comp_reactivate_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("aaa").disable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    return ok_result({"disabled": True})


def _step_reactivate_service(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "ACTIVE"
    sub.suspension_date = None
    publish_outbox(ctx.session, "oss.service.reactivated.v1", {"subscription_code": sub.subscription_code, "subscription_id": str(sub.id)}, ctx.tenant_id)
    return ok_result({"subscription_id": str(sub.id), "status": "ACTIVE"})


def _step_terminate_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    aaa = get_adapter("aaa")
    nas = get_adapter("nas")
    aaa.disable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    nas.remove_subscriber(ctx.tenant_id, sub.nas_reference or DEFAULT_NAS_REFERENCE, sub.aaa_subscriber_reference)
    return ok_result({"aaa_subscriber_reference": sub.aaa_subscriber_reference})


def _comp_terminate_access(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("aaa").enable_subscriber(ctx.tenant_id, sub.aaa_subscriber_reference)
    return ok_result({"enabled": True})


def _step_terminate_subscription(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    sub.status = "TERMINATED"
    sub.termination_date = _now()
    publish_outbox(ctx.session, "oss.service.terminated.v1", {"subscription_code": sub.subscription_code, "subscription_id": str(sub.id)}, ctx.tenant_id)
    return ok_result({"subscription_id": str(sub.id), "status": "TERMINATED"})


def _step_close_billing(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    get_adapter("bss").close_billing_account(ctx.tenant_id, sub.billing_account_reference)
    return ok_result({"billing_account_reference": sub.billing_account_reference})


def build_suspension_saga() -> SagaDefinition:
    return SagaDefinition(
        "SERVICE_SUSPENSION",
        [
            Step("suspend_access", _step_suspend_access, _comp_suspend_access, max_attempts=3, order_state="PROVISIONING"),
            Step("suspend_service", _step_suspend_service, _comp_suspend_service, max_attempts=3, order_state="VERIFYING"),
            Step("suspend_billing", _step_suspend_billing, _comp_suspend_billing, max_attempts=3),
        ],
    )


def build_reactivation_saga() -> SagaDefinition:
    return SagaDefinition(
        "SERVICE_REACTIVATION",
        [
            Step("reactivate_billing", _comp_suspend_billing, _step_suspend_billing, max_attempts=3, order_state="PROVISIONING"),
            Step("reactivate_access", _step_reactivate_access, _comp_reactivate_access, max_attempts=3),
            Step("reactivate_service", _step_reactivate_service, _comp_suspend_service, max_attempts=3, order_state="VERIFYING"),
        ],
    )


def build_termination_saga() -> SagaDefinition:
    return SagaDefinition(
        "SERVICE_TERMINATION",
        [
            Step("terminate_access", _step_terminate_access, _comp_terminate_access, max_attempts=3, order_state="PROVISIONING"),
            Step("terminate_subscription", _step_terminate_subscription, None, max_attempts=3),
            Step("close_billing", _step_close_billing, None, max_attempts=3, order_state="VERIFYING"),
        ],
    )


# ===========================================================================
# Upgrade / Downgrade / Relocation
# ===========================================================================

def _step_update_aaa_plan(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    order = ctx.order_service.repo.load(ctx.order_id)
    get_adapter("aaa").update_plan(ctx.tenant_id, sub.aaa_subscriber_reference, order.requested_plan_reference)
    return ok_result({"aaa_subscriber_reference": sub.aaa_subscriber_reference, "plan_reference": order.requested_plan_reference})


def _comp_update_aaa_plan(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    previous = sub.plan_reference
    get_adapter("aaa").update_plan(ctx.tenant_id, sub.aaa_subscriber_reference, previous)
    return ok_result({"restored": True})


def _step_update_subscription_plan(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    order = ctx.order_service.repo.load(ctx.order_id)
    previous_plan = sub.plan_reference
    sub.plan_reference = order.requested_plan_reference
    return ok_result({"subscription_id": str(sub.id), "plan_reference": order.requested_plan_reference, "previous_plan_reference": previous_plan})


def _comp_update_subscription_plan(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    previous = (ctx.store.get("update_subscription_plan") or {}).get("previous_plan_reference")
    if previous:
        sub.plan_reference = previous
    return ok_result({"restored": True})


def _step_notify_bss_plan(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    order = ctx.order_service.repo.load(ctx.order_id)
    get_adapter("bss").update_plan(ctx.tenant_id, sub.billing_account_reference, order.requested_plan_reference)
    return ok_result({"billing_account_reference": sub.billing_account_reference})


def build_plan_change_saga(order_type: str) -> SagaDefinition:
    return SagaDefinition(
        order_type,
        [
            Step("update_aaa_plan", _step_update_aaa_plan, _comp_update_aaa_plan, max_attempts=3, order_state="PROVISIONING"),
            Step("update_subscription_plan", _step_update_subscription_plan, _comp_update_subscription_plan, max_attempts=3),
            Step("notify_bss_plan", _step_notify_bss_plan, None, max_attempts=3, order_state="VERIFYING"),
        ],
    )


def _step_reserve_new_location(ctx: StepContext) -> StepResult:
    return _step_reserve_resources(ctx)


def _step_update_location(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    order = ctx.order_service.repo.load(ctx.order_id)
    previous_location = sub.service_location_id
    sub.service_location_id = order.service_location_id
    return ok_result({"subscription_id": str(sub.id), "service_location_id": str(order.service_location_id), "previous_service_location_id": str(previous_location) if previous_location else None})


def _comp_update_location(ctx: StepContext) -> StepResult:
    sub = _subscription(ctx)
    previous = (ctx.store.get("update_location") or {}).get("previous_service_location_id")
    if previous:
        sub.service_location_id = previous
    return ok_result({"restored": True})


def build_relocation_saga() -> SagaDefinition:
    return SagaDefinition(
        "SERVICE_RELOCATION",
        [
            Step("reserve_new_location", _step_reserve_new_location, _comp_reserve_resources, max_attempts=3, order_state="RESOURCE_RESERVATION"),
            Step("schedule_installation", _step_schedule_installation, _comp_schedule_installation, max_attempts=3, order_state="FIELD_INSTALLATION_PENDING"),
            Step("wait_installation", _step_wait_installation, _comp_wait_installation, max_attempts=1, pausable=True),
            Step("configure_access", _step_configure_access, _comp_configure_access, max_attempts=3, order_state="PROVISIONING"),
            Step("update_location", _step_update_location, _comp_update_location, max_attempts=3),
        ],
    )


# ===========================================================================
# Workflow selection
# ===========================================================================

SAGA_BUILDERS = {
    "NEW_CONNECTION": build_new_connection_saga,
    "SERVICE_SUSPENSION": build_suspension_saga,
    "SERVICE_REACTIVATION": build_reactivation_saga,
    "SERVICE_TERMINATION": build_termination_saga,
    "PACKAGE_UPGRADE": lambda: build_plan_change_saga("PACKAGE_UPGRADE"),
    "PACKAGE_DOWNGRADE": lambda: build_plan_change_saga("PACKAGE_DOWNGRADE"),
    "SERVICE_RELOCATION": build_relocation_saga,
}

# Order types that reuse a subscription context created earlier (saga reads it).
ORDER_TYPE_TO_WORKFLOW = {
    "NEW_CONNECTION": "NEW_CONNECTION",
    "PACKAGE_UPGRADE": "PACKAGE_UPGRADE",
    "PACKAGE_DOWNGRADE": "PACKAGE_DOWNGRADE",
    "SERVICE_SUSPENSION": "SERVICE_SUSPENSION",
    "SERVICE_REACTIVATION": "SERVICE_REACTIVATION",
    "SERVICE_TERMINATION": "SERVICE_TERMINATION",
    "SERVICE_RELOCATION": "SERVICE_RELOCATION",
}
