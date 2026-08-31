"""Governed device actions with authorization workflow: refresh, reapply
profile, reboot, factory reset, connection request, credential rotation.
Elevated actions (factory reset, firmware upgrade) require approval."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import ssrf
from ..domain.exceptions import ActionError, AuthorizationRequiredError, NotFoundError, StateTransitionError
from ..enums import DEVICE_ACTION_TYPES, ELEVATED_ACTIONS
from ..integrations.acs import get_acs_client
from ..models import DeviceAction, ManagedCpe
from ..state_machine import device_action_transition
from . import device_service
from .audit_service import append_event, audit, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_action_or_404(session: Session, tenant_id, action_id: uuid.UUID) -> DeviceAction:
    action = session.get(DeviceAction, action_id)
    if action is None or action.tenant_id != tenant_id:
        raise NotFoundError("device action not found")
    return action


def create_action(session: Session, tenant_id, cpe_id: uuid.UUID, *, action_type: str, parameters: dict | None = None,
                  requested_by: str = "system", actor: str = "system", elevated: bool = False,
                  idempotency_key: str | None = None, correlation_id: str | None = None) -> DeviceAction:
    request_id = correlation(correlation_id)
    if idempotency_key:
        existing = session.scalars(select(DeviceAction).where(
            DeviceAction.tenant_id == tenant_id, DeviceAction.idempotency_key == idempotency_key)).first()
        if existing is not None:
            return existing
    action_type = action_type.upper()
    if action_type not in DEVICE_ACTION_TYPES:
        raise ActionError(f"invalid device action {action_type!r}")
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    requires_approval = elevated or action_type in ELEVATED_ACTIONS
    if action_type == "CONNECTION_REQUEST":
        url = (parameters or {}).get("connection_request_url") or device.connection_request_url_ref
        if url:
            ssrf.validate_connection_request_url(url)
    action = DeviceAction(
        tenant_id=tenant_id, cpe_id=cpe_id, action_type=action_type, state="REQUESTED",
        parameters=parameters or {}, requested_by=requested_by, requires_approval=requires_approval,
        idempotency_key=idempotency_key, correlation_id=request_id)
    session.add(action)
    session.flush()
    if requires_approval:
        _transition(action, "AUTHORIZATION_REQUIRED")
    append_event(session, device, "action.requested", payload={"action_id": str(action.id),
                                                               "action_type": action_type},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    session.flush()
    return action


def _transition(action: DeviceAction, target: str) -> None:
    try:
        device_action_transition(action.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    action.state = target


def approve_action(session: Session, tenant_id, action_id: uuid.UUID, *, approver: str = "system",
                   correlation_id: str | None = None) -> DeviceAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.state != "AUTHORIZATION_REQUIRED":
        raise ActionError(f"action does not require approval (state {action.state})")
    _transition(action, "APPROVED")
    action.approved_by = approver
    action.approved_at = _now()
    device = session.get(ManagedCpe, action.cpe_id)
    audit(session, tenant_id, "device.action.approved", "device_action", str(action.id), actor=approver,
          payload={"action_type": action.action_type}, correlation_id=correlation_id or action.correlation_id)
    append_event(session, device, "action.approved", payload={"action_id": str(action.id)},
                 actor_type="agent", actor_id=approver, correlation_id=correlation_id or action.correlation_id)
    session.flush()
    return action


def execute_action(session: Session, tenant_id, action_id: uuid.UUID, *, actor: str = "system",
                   correlation_id: str | None = None) -> DeviceAction:
    action = get_action_or_404(session, tenant_id, action_id)
    device = session.get(ManagedCpe, action.cpe_id)
    if action.state == "AUTHORIZATION_REQUIRED":
        raise AuthorizationRequiredError("action requires approval before execution")
    if action.state in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"):
        raise ActionError(f"action already in terminal state {action.state}")
    if action.state in ("REQUESTED", "APPROVED"):
        _transition(action, "QUEUED")
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    task_id = _dispatch(client, action, device)
    action.genieacs_task_id = task_id
    action.executed_at = _now()
    _transition(action, "EXECUTING")
    if action.action_type in ("REBOOT", "FACTORY_RESET"):
        # Attempt a connection request; failures keep the task queued for the
        # next Inform rather than failing the action outright.
        outcome = client.trigger_connection_request(device.acs_device_id)
        action.connection_request_outcome = outcome
    event_name = "cpe.rebooted.v1" if action.action_type == "REBOOT" else "action.executed"
    if event_name == "cpe.rebooted.v1":
        outbox(session, event_name, tenant_id, correlation_id or action.correlation_id,
               {"cpe_id": str(action.cpe_id), "action_id": str(action.id)})
    append_event(session, device, "action.executed", payload={"action_id": str(action.id),
                                                              "action_type": action.action_type,
                                                              "task_id": task_id},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    session.flush()
    return action


def _dispatch(client, action: DeviceAction, device: ManagedCpe) -> str:
    action_type = action.action_type
    if action_type == "REFRESH_PARAMETERS":
        return client.refresh_object(device.acs_device_id, (action.parameters or {}).get("path", "Device."))
    if action_type == "REBOOT":
        return client.reboot(device.acs_device_id)
    if action_type == "FACTORY_RESET":
        return client.factory_reset(device.acs_device_id)
    if action_type == "DOWNLOAD_CONFIGURATION":
        return client.download_file(device.acs_device_id, (action.parameters or {}).get("url", ""), "3 Configuration")
    if action_type == "CHANGE_PARAMETER":
        return client.set_parameters(device.acs_device_id, {action.parameters.get("path"): action.parameters.get("value")})
    if action_type == "CONNECTION_REQUEST":
        url = (action.parameters or {}).get("connection_request_url") or device.connection_request_url_ref
        if url:
            ssrf.validate_connection_request_url(url)
            client.trigger_connection_request(device.acs_device_id, url=url)
        else:
            client.trigger_connection_request(device.acs_device_id)
        return f"cr-{uuid.uuid4().hex[:8]}"
    if action_type == "TRIGGER_DIAGNOSTIC":
        return client.create_task(device.acs_device_id, "diag", action.parameters or {})["task_id"]
    raise ActionError(f"no dispatch for action type {action_type!r}")


def complete_action(session: Session, tenant_id, action_id: uuid.UUID, *, ok: bool, result: dict | None = None,
                    actor: str = "system", correlation_id: str | None = None) -> DeviceAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
        return action
    device = session.get(ManagedCpe, action.cpe_id)
    if ok:
        _transition(action, "SUCCEEDED")
        action.result_summary = result or {}
        action.completed_at = _now()
        append_event(session, device, "action.succeeded", payload={"action_id": str(action.id)},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    else:
        _transition(action, "FAILED")
        action.failure_code = (result or {}).get("code") or "ACTION_FAILED"
        action.failure_detail = (result or {}).get("detail") or "device action failed"
        append_event(session, device, "action.failed", payload={"action_id": str(action.id),
                                                                "code": action.failure_code},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    session.flush()
    return action
