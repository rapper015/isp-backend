"""Durable saga orchestration engine.

Persists saga state (instance, steps, attempts, workflow events) in the
database so sagas survive worker restarts; side effects are idempotent and
protected by database compare-and-set in the resource service. On non-retryable
failure or exhausted retries the engine runs reverse-order compensation and can
raise a manual intervention."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import publish_outbox
from ..integrations.base import RetryableAdapterError, StepResult, fail_result, ok_result
from ..models import ManualIntervention, SagaInstance, SagaStep, SagaStepAttempt, WorkflowEvent
from ..state_machine import saga_transition, step_transition
from .order_service import OrderService
from .resource_service import ResourceService

TERMINAL_SAGA_STATES = {"COMPLETED", "COMPENSATED", "FAILED", "CANCELLED", "MANUAL_INTERVENTION"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StepContext:
    session: Session
    tenant_id: uuid.UUID
    order_id: uuid.UUID
    order_service: OrderService
    resource_service: ResourceService
    store: dict = field(default_factory=dict)


@dataclass
class Step:
    name: str
    execute: Callable[[StepContext], StepResult]
    compensate: Callable[[StepContext], StepResult] | None = None
    max_attempts: int = 3
    timeout_seconds: int = 120
    order_state: str | None = None  # order state to ensure after success
    pausable: bool = False  # retryable failure pauses the saga instead of retrying


class SagaDefinition:
    def __init__(self, workflow_type: str, steps: list[Step]):
        self.workflow_type = workflow_type
        self.steps = steps

    def get(self, name: str) -> Step:
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)


class SagaEngine:
    def __init__(self, session: Session, order_service: OrderService | None = None, resource_service: ResourceService | None = None):
        self.session = session
        self.order_service = order_service or OrderService(session)
        self.resource_service = resource_service or ResourceService(session)
        self.definitions: dict[str, SagaDefinition] = {}

    def register(self, definition: SagaDefinition) -> None:
        self.definitions[definition.workflow_type] = definition

    # -- lifecycle ----------------------------------------------------------
    def start(self, tenant_id: uuid.UUID, order_id: uuid.UUID, workflow_type: str, correlation_id: str | None = None) -> SagaInstance:
        definition = self.definitions.get(workflow_type)
        if definition is None:
            raise ValueError(f"no saga registered for workflow {workflow_type!r}")
        saga = SagaInstance(
            tenant_id=tenant_id,
            order_id=order_id,
            workflow_type=workflow_type,
            state="PENDING",
            correlation_id=correlation_id,
        )
        self.session.add(saga)
        self.session.flush()
        for index, step in enumerate(definition.steps):
            self.session.add(
                SagaStep(
                    tenant_id=tenant_id,
                    saga_id=saga.id,
                    step_index=index,
                    step_name=step.name,
                    state="PENDING",
                    max_attempts=step.max_attempts,
                    timeout_seconds=step.timeout_seconds,
                )
            )
        publish_outbox(self.session, "oss.workflow.started.v1", {"workflow_type": workflow_type, "order_id": str(order_id)}, tenant_id, correlation_id)
        self.session.flush()
        return saga

    def advance(self, saga_id: uuid.UUID) -> str:
        saga = self._load_saga(saga_id)
        if saga.state in TERMINAL_SAGA_STATES:
            return saga.state
        definition = self.definitions[saga.workflow_type]
        steps = self._load_steps(saga_id)
        ctx = self._build_context(saga, steps)
        if saga.state == "PENDING":
            saga.state = saga_transition(saga.state, "RUNNING")

        for step_row in steps:
            if step_row.state != "PENDING":
                continue
            step_def = definition.get(step_row.step_name)
            outcome = self._execute_with_retries(saga, step_row, step_def, ctx)
            if outcome in ("COMPENSATED", "MANUAL_INTERVENTION", "FAILED", "PAUSED"):
                return saga.state
            # step completed
            if step_def.order_state:
                self.order_service.ensure_state(saga.order_id, step_def.order_state, actor="saga", correlation_id=saga.correlation_id)
            saga.current_step_index = step_row.step_index + 1
            self.session.commit()

        saga.state = saga_transition("RUNNING", "COMPLETED")
        saga.completed_at = _now()
        self.order_service.ensure_state(saga.order_id, "COMPLETED", actor="saga", correlation_id=saga.correlation_id)
        publish_outbox(self.session, "oss.workflow.completed.v1", {"workflow_type": saga.workflow_type, "order_id": str(saga.order_id)}, saga.tenant_id, saga.correlation_id)
        self.session.commit()
        return saga.state

    def resume(self, saga_id: uuid.UUID, resolved_by: str = "operator") -> str:
        """After manual intervention or a failed state, reset failed steps to
        PENDING and re-run from the first incomplete step."""
        saga = self._load_saga(saga_id)
        if saga.state == "MANUAL_INTERVENTION":
            saga.state = saga_transition(saga.state, "RUNNING")
        elif saga.state == "FAILED":
            saga.state = saga_transition(saga.state, "RUNNING")
        for step in self._load_steps(saga_id):
            if step.state in ("FAILED", "PENDING", "RUNNING"):
                step.state = "PENDING"
        interventions = list(self.session.scalars(select(ManualIntervention).where(ManualIntervention.saga_id == saga_id, ManualIntervention.status == "OPEN")))
        for intervention in interventions:
            intervention.status = "RESOLVED"
            intervention.resolved_by = resolved_by
            intervention.resolved_at = _now()
        self.session.commit()
        return self.advance(saga_id)

    def compensate(self, saga_id: uuid.UUID, reason: str | None = None) -> str:
        saga = self._load_saga(saga_id)
        if saga.state in ("COMPENSATED", "ROLLED_BACK", "COMPLETED"):
            return saga.state
        if saga.state == "PENDING":
            saga.state = saga_transition(saga.state, "RUNNING")
        if saga.state == "RUNNING":
            saga.state = saga_transition(saga.state, "COMPENSATING")
        elif saga.state == "FAILED":
            saga.state = saga_transition(saga.state, "COMPENSATING")
        definition = self.definitions[saga.workflow_type]
        steps = sorted(self._load_steps(saga_id), key=lambda s: s.step_index, reverse=True)
        ctx = self._build_context(saga, steps)
        publish_outbox(self.session, "oss.workflow.compensating.v1", {"reason": reason}, saga.tenant_id, saga.correlation_id)
        for step_row in steps:
            step_def = definition.get(step_row.step_name)
            if step_row.state == "PENDING":
                # Never-started steps are skipped during compensation.
                step_row.state = step_transition(step_row.state, "SKIPPED")
                self.session.commit()
                continue
            if step_def.compensate is None:
                continue
            if step_row.state not in ("COMPLETED", "FAILED", "RUNNING"):
                continue
            try:
                result = step_def.compensate(ctx)
            except Exception as error:  # noqa: BLE001
                result = fail_result("COMPENSATION_ERROR", str(error), retryable=False)
            if result.ok:
                step_row.state = step_transition(step_row.state, "COMPENSATED")
                self.session.add(WorkflowEvent(saga_id=saga.id, event_type="oss.workflow.step_compensated", step_name=step_row.step_name, payload=result.output, correlation_id=saga.correlation_id))
                self.session.commit()
            else:
                return self.mark_manual_intervention(saga_id, reason=f"compensation of {step_row.step_name} failed: {result.error_detail}")
        saga.state = saga_transition("COMPENSATING", "COMPENSATED")
        self._drive_order_compensation(saga.order_id)
        self.session.commit()
        return saga.state

    def mark_manual_intervention(self, saga_id: uuid.UUID, reason: str) -> str:
        saga = self._load_saga(saga_id)
        if saga.state not in ("RUNNING", "FAILED", "COMPENSATING"):
            saga.state = saga_transition(saga.state, "MANUAL_INTERVENTION")
        elif saga.state == "RUNNING":
            saga.state = saga_transition(saga.state, "MANUAL_INTERVENTION")
        elif saga.state == "FAILED":
            saga.state = saga_transition(saga.state, "MANUAL_INTERVENTION")
        elif saga.state == "COMPENSATING":
            saga.state = saga_transition(saga.state, "MANUAL_INTERVENTION")
        self._drive_order_to_manual(saga.order_id)
        self.session.add(ManualIntervention(tenant_id=saga.tenant_id, order_id=saga.order_id, saga_id=saga.id, reason=reason, evidence={"workflow_type": saga.workflow_type}, status="OPEN"))
        publish_outbox(self.session, "oss.workflow.manual_intervention.v1", {"reason": reason}, saga.tenant_id, saga.correlation_id)
        saga.failure_reason = reason
        self.session.commit()
        return saga.state

    def _drive_order_compensation(self, order_id: uuid.UUID) -> None:
        """Drive the order through FAILED -> COMPENSATING -> ROLLED_BACK using
        validated transitions (tolerantly skipping states already reached)."""
        for target in ("FAILED", "COMPENSATING", "ROLLED_BACK"):
            try:
                self.order_service.ensure_state(order_id, target, actor="saga")
            except Exception:  # noqa: BLE001 - compensation must not raise
                pass

    def _drive_order_to_manual(self, order_id: uuid.UUID) -> None:
        for target in ("FAILED", "MANUAL_INTERVENTION_REQUIRED"):
            try:
                self.order_service.ensure_state(order_id, target, actor="saga")
            except Exception:  # noqa: BLE001
                pass

    def requeue_stale(self, now: datetime | None = None) -> list[uuid.UUID]:
        """Worker: requeue RUNNING steps whose timeout elapsed (crash/stall)."""
        from datetime import timedelta

        now = now or _now()
        running = list(
            self.session.scalars(
                select(SagaStep).where(
                    SagaStep.state == "RUNNING",
                    SagaStep.started_at.is_not(None),
                )
            )
        )
        stale = [step for step in running if step.started_at + timedelta(seconds=step.timeout_seconds) <= now]
        for step in stale:
            step.state = "PENDING"
        self.session.commit()
        return list({step.saga_id for step in stale})

    # -- execution internals ------------------------------------------------
    def _execute_with_retries(self, saga: SagaInstance, step_row: SagaStep, step_def: Step, ctx: StepContext) -> str:
        """Runs a step with retries. Returns 'ok' (implicitly) or a terminal
        outcome string."""
        for attempt in range(1, step_def.max_attempts + 1):
            step_row.state = step_transition(step_row.state, "RUNNING")
            step_row.started_at = _now()
            step_row.attempt_count = attempt
            attempt_row = SagaStepAttempt(
                tenant_id=saga.tenant_id,
                saga_step_id=step_row.id,
                attempt_number=attempt,
                status="RUNNING",
            )
            self.session.add(attempt_row)
            self.session.flush()
            try:
                result = step_def.execute(ctx)
            except RetryableAdapterError as error:
                result = fail_result("RETRYABLE_ERROR", str(error), retryable=True)
            except Exception as error:  # noqa: BLE001
                result = fail_result("STEP_ERROR", str(error), retryable=False)
            if result.ok:
                attempt_row.status = "COMPLETED"
                attempt_row.completed_at = _now()
                attempt_row.output = result.output
                step_row.state = step_transition(step_row.state, "COMPLETED")
                step_row.output = result.output
                step_row.completed_at = _now()
                step_row.error_code = None
                step_row.last_error = None
                ctx.store[step_row.step_name] = result.output
                self.session.add(WorkflowEvent(saga_id=saga.id, event_type="oss.workflow.step_completed", step_name=step_row.step_name, payload=result.output, correlation_id=saga.correlation_id))
                self.session.commit()
                return "ok"
            attempt_row.status = "FAILED"
            attempt_row.completed_at = _now()
            attempt_row.error_code = result.error_code
            attempt_row.error_detail = result.error_detail
            step_row.error_code = result.error_code
            step_row.last_error = result.error_detail
            self.session.add(WorkflowEvent(saga_id=saga.id, event_type="oss.workflow.step_failed", step_name=step_row.step_name, payload={"error_code": result.error_code, "error_detail": result.error_detail, "attempt": attempt}, correlation_id=saga.correlation_id))
            self.session.commit()
            if step_def.pausable and result.retryable:
                # Gate step (payment/installation): pause until an external
                # condition advances it; the worker re-invokes advance() later.
                step_row.state = "PENDING"
                self.session.commit()
                return "PAUSED"
            if not result.retryable or attempt >= step_def.max_attempts:
                step_row.state = step_transition(step_row.state, "FAILED")
                self.session.commit()
                if any(other.state in ("COMPLETED", "RUNNING") for other in self._load_steps(saga.id) if other.id != step_row.id) and self._has_compensation(saga.id):
                    return self.compensate(saga.id, reason=result.error_detail)
                return self.mark_manual_intervention(saga.id, reason=f"step {step_row.step_name} failed: {result.error_detail}")
            # retryable, attempts remain: reset to PENDING then loop
            step_row.state = "PENDING"
            self.session.commit()
        return "FAILED"

    def _has_compensation(self, saga_id: uuid.UUID) -> bool:
        saga = self._load_saga(saga_id)
        definition = self.definitions[saga.workflow_type]
        return any(step.compensate is not None for step in definition.steps)

    def _build_context(self, saga: SagaInstance, steps: list[SagaStep]) -> StepContext:
        store: dict = {}
        for step_row in steps:
            if step_row.state == "COMPLETED":
                store[step_row.step_name] = step_row.output
        return StepContext(
            session=self.session,
            tenant_id=saga.tenant_id,
            order_id=saga.order_id,
            order_service=self.order_service,
            resource_service=self.resource_service,
            store=store,
        )

    def _load_saga(self, saga_id: uuid.UUID) -> SagaInstance:
        saga = self.session.get(SagaInstance, saga_id)
        if saga is None:
            raise RuntimeError(f"saga {saga_id} not found")
        return saga

    def _load_steps(self, saga_id: uuid.UUID) -> list[SagaStep]:
        return list(self.session.scalars(select(SagaStep).where(SagaStep.saga_id == saga_id).order_by(SagaStep.step_index)))
