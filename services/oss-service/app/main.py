"""OSS Service — order management, resource reservation and provisioning
workflow API (Milestone 2)."""
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models  # noqa: F401  (register all tables)
from .database import Base, SessionLocal, engine
from .enums import ORDER_STATES, RESOURCE_TYPES
from .models import ManualIntervention, Order, ResourceReservation, ServiceSubscription
from .schemas import (
    CapacityResponse,
    InterventionResolveRequest,
    OrderCreate,
    OrderEventResponse,
    OrderResponse,
    ReservationResponse,
    ResourceRegister,
    SagaDetailResponse,
    SagaStepResponse,
    StatusHistoryResponse,
    SubscriptionResponse,
    TransitionRequest,
    ValidateResponse,
    ValidActionsResponse,
)
from .security import management_auth
from .services.activation import ProvisioningService
from .services.order_service import OrderService, valid_actions as order_valid_actions
from .services.resource_service import ResourceService


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="OSS Service", version="2.0.0", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def provisioning(session: Session = Depends(db)) -> ProvisioningService:
    return ProvisioningService(session)


def _order_or_404(session: Session, order_id: UUID) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    return order


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "oss-service")}


@app.get("/status")
def service_status():
    return {"service": "oss", "phase": "milestone-2-provisioning"}


# ===========================================================================
# Orders
# ===========================================================================

@app.post("/api/oss/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_order(payload: OrderCreate, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.create_order(
            payload.tenant_id,
            order_type=payload.order_type,
            customer_id=payload.customer_id,
            service_location_id=payload.service_location_id,
            requested_plan_reference=payload.requested_plan_reference,
            previous_plan_reference=payload.previous_plan_reference,
            requested_activation_date=payload.requested_activation_date,
            priority=payload.priority,
            source_channel=payload.source_channel,
            franchise_id=payload.franchise_id,
            reseller_id=payload.reseller_id,
            requested_snapshot=payload.requested_snapshot,
            actor=payload.actor,
            correlation_id=payload.correlation_id,
        )
        if payload.service_subscription_id:
            order.service_subscription_id = payload.service_subscription_id
        session.commit()
        session.refresh(order)
        return order
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/oss/orders", response_model=list[OrderResponse], dependencies=[Depends(management_auth)])
def list_orders(
    tenant_id: UUID | None = Query(default=None),
    order_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    session: Session = Depends(db),
):
    stmt = select(Order).order_by(Order.created_at.desc())
    if tenant_id:
        stmt = stmt.where(Order.tenant_id == tenant_id)
    if order_type:
        stmt = stmt.where(Order.order_type == order_type)
    if state:
        if state not in ORDER_STATES:
            raise HTTPException(422, f"invalid order state {state!r}")
        stmt = stmt.where(Order.state == state)
    return list(session.scalars(stmt))


@app.get("/api/oss/orders/{order_id}", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def get_order(order_id: UUID, session: Session = Depends(db)):
    return _order_or_404(session, order_id)


@app.post("/api/oss/orders/{order_id}/submit", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def submit_order(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.submit(order_id, actor=payload.actor if payload else "system", correlation_id=payload.correlation_id if payload else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/validate", response_model=ValidateResponse, dependencies=[Depends(management_auth)])
def validate_order(order_id: UUID, session: Session = Depends(db)):
    ps = provisioning(session)
    try:
        result = ps.validate_order(order_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    order = _order_or_404(session, order_id)
    return ValidateResponse(order_id=order.id, order_number=order.order_number, result_state=result, message=f"order validation resulted in {result}")


@app.post("/api/oss/orders/{order_id}/approve-payment", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def approve_payment(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.approve_payment(order_id, actor=payload.actor if payload else "system", correlation_id=payload.correlation_id if payload else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/fulfil", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def fulfil_order(order_id: UUID, session: Session = Depends(db)):
    ps = provisioning(session)
    try:
        order, _saga, _state = ps.start_workflow(order_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/cancel", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def cancel_order(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    order = _order_or_404(session, order_id)
    try:
        if order.state == "DRAFT":
            order = service.cancel(order_id, reason=(payload.reason if payload else None) or "cancelled", actor=payload.actor if payload else "system")
        else:
            order = service.request_cancel(order_id, reason=(payload.reason if payload else None) or "cancellation requested", actor=payload.actor if payload else "system")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/retry", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def retry_order(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.retry(order_id, actor=payload.actor if payload else "system", correlation_id=payload.correlation_id if payload else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/resume", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def resume_order(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.resume(order_id, actor=payload.actor if payload else "system", correlation_id=payload.correlation_id if payload else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.post("/api/oss/orders/{order_id}/compensate", response_model=OrderResponse, dependencies=[Depends(management_auth)])
def compensate_order(order_id: UUID, payload: TransitionRequest | None = None, session: Session = Depends(db)):
    service = OrderService(session)
    try:
        order = service.compensate(order_id, reason=(payload.reason if payload else None) or "compensating after failure", actor=payload.actor if payload else "system")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    session.refresh(order)
    return order


@app.get("/api/oss/orders/{order_id}/valid-actions", response_model=ValidActionsResponse, dependencies=[Depends(management_auth)])
def valid_actions(order_id: UUID, session: Session = Depends(db)):
    order = _order_or_404(session, order_id)
    return ValidActionsResponse(order_id=order.id, state=order.state, valid_actions=order_valid_actions(order.state))


@app.get("/api/oss/orders/{order_id}/events", response_model=list[OrderEventResponse], dependencies=[Depends(management_auth)])
def order_events(order_id: UUID, session: Session = Depends(db)):
    _order_or_404(session, order_id)
    return OrderService(session).events(order_id)


@app.get("/api/oss/orders/{order_id}/history", response_model=list[StatusHistoryResponse], dependencies=[Depends(management_auth)])
def order_history(order_id: UUID, session: Session = Depends(db)):
    _order_or_404(session, order_id)
    return OrderService(session).history(order_id)


# ===========================================================================
# Workflows (sagas)
# ===========================================================================

@app.get("/api/oss/orders/{order_id}/workflows", response_model=list[SagaDetailResponse], dependencies=[Depends(management_auth)])
def order_workflows(order_id: UUID, session: Session = Depends(db)):
    from .models import SagaInstance

    _order_or_404(session, order_id)
    return list(session.scalars(select(SagaInstance).where(SagaInstance.order_id == order_id).order_by(SagaInstance.created_at)))


@app.get("/api/oss/workflows/{saga_id}", response_model=SagaDetailResponse, dependencies=[Depends(management_auth)])
def workflow_detail(saga_id: UUID, session: Session = Depends(db)):
    from .models import SagaInstance

    saga = session.get(SagaInstance, saga_id)
    if saga is None:
        raise HTTPException(404, "workflow not found")
    return saga


@app.get("/api/oss/workflows/{saga_id}/steps", response_model=list[SagaStepResponse], dependencies=[Depends(management_auth)])
def workflow_steps(saga_id: UUID, session: Session = Depends(db)):
    from .models import SagaInstance, SagaStep

    if session.get(SagaInstance, saga_id) is None:
        raise HTTPException(404, "workflow not found")
    return list(session.scalars(select(SagaStep).where(SagaStep.saga_id == saga_id).order_by(SagaStep.step_index)))


@app.post("/api/oss/workflows/{saga_id}/resume", response_model=SagaDetailResponse, dependencies=[Depends(management_auth)])
def workflow_resume(saga_id: UUID, session: Session = Depends(db)):
    from .models import SagaInstance

    ps = provisioning(session)
    ps.resume(saga_id)
    saga = session.get(SagaInstance, saga_id)
    session.commit()
    return saga


# ===========================================================================
# Resources
# ===========================================================================

@app.post("/api/oss/resources/register", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def register_resource(payload: ResourceRegister, session: Session = Depends(db)):
    if payload.resource_type not in RESOURCE_TYPES:
        raise HTTPException(422, f"invalid resource type {payload.resource_type!r}")
    service = ResourceService(session)
    row = service.register(payload.tenant_id, payload.resource_type, payload.resource_key, payload.metadata)
    session.commit()
    return {"id": str(row.id), "resource_type": row.resource_type, "resource_key": row.resource_key, "status": row.status}


@app.get("/api/oss/resources/capacity", response_model=CapacityResponse, dependencies=[Depends(management_auth)])
def resource_capacity(tenant_id: UUID = Query(...), resource_type: str | None = Query(default=None), session: Session = Depends(db)):
    return CapacityResponse(capacity=ResourceService(session).capacity(tenant_id, resource_type))


@app.get("/api/oss/resources/reservations", response_model=list[ReservationResponse], dependencies=[Depends(management_auth)])
def list_reservations(tenant_id: UUID = Query(...), order_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    stmt = select(ResourceReservation).where(ResourceReservation.tenant_id == tenant_id).order_by(ResourceReservation.reserved_at.desc())
    if order_id:
        stmt = stmt.where(ResourceReservation.order_id == order_id)
    return list(session.scalars(stmt))


# ===========================================================================
# Subscriptions
# ===========================================================================

@app.get("/api/oss/subscriptions", response_model=list[SubscriptionResponse], dependencies=[Depends(management_auth)])
def list_subscriptions(tenant_id: UUID | None = Query(default=None), status: str | None = Query(default=None), session: Session = Depends(db)):
    stmt = select(ServiceSubscription).order_by(ServiceSubscription.created_at)
    if tenant_id:
        stmt = stmt.where(ServiceSubscription.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ServiceSubscription.status == status)
    return list(session.scalars(stmt))


@app.get("/api/oss/subscriptions/{subscription_id}", response_model=SubscriptionResponse, dependencies=[Depends(management_auth)])
def get_subscription(subscription_id: UUID, session: Session = Depends(db)):
    sub = session.get(ServiceSubscription, subscription_id)
    if sub is None:
        raise HTTPException(404, "subscription not found")
    return sub


# ===========================================================================
# Manual interventions
# ===========================================================================

@app.get("/api/oss/manual-interventions", dependencies=[Depends(management_auth)])
def list_interventions(status: str = "OPEN", session: Session = Depends(db)):
    return list(session.scalars(select(ManualIntervention).where(ManualIntervention.status == status).order_by(ManualIntervention.created_at)))


@app.post("/api/oss/manual-interventions/{intervention_id}/resolve", response_model=SagaDetailResponse, dependencies=[Depends(management_auth)])
def resolve_intervention(intervention_id: UUID, payload: InterventionResolveRequest | None = None, session: Session = Depends(db)):
    from .models import SagaInstance

    intervention = session.get(ManualIntervention, intervention_id)
    if intervention is None:
        raise HTTPException(404, "intervention not found")
    ps = provisioning(session)
    ps.resume(intervention.saga_id, resolved_by=payload.resolved_by if payload else "operator")
    session.commit()
    saga = session.get(SagaInstance, intervention.saga_id)
    if saga is None:
        raise HTTPException(404, "workflow not found")
    return saga

