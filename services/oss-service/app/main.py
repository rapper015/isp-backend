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
from .services.assets_service import (
    AssetsService,
    ConfigService,
    EnterpriseService,
    InfraService,
    InventoryDriftService,
    SecurityService,
    TelemetryService,
    TrafficService,
    VendorService,
)


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


# ===========================================================================
# Assets, vendors, splitters (Batch 3: 205, 208, 231, 1138)
# ===========================================================================

@app.post("/api/oss/assets/register", status_code=201, dependencies=[Depends(management_auth)])
def register_asset(payload: dict, session: Session = Depends(db)):
    tenant_id = UUID(str(payload.get("tenant_id")))
    asset = AssetsService.register_asset(session, tenant_id, payload, by="operator")
    return {"id": str(asset.id), "asset_type": asset.asset_type, "name": asset.name,
            "firmware_version": asset.firmware_version, "site_owner": asset.site_owner,
            "status": asset.status}


@app.get("/api/oss/assets", dependencies=[Depends(management_auth)])
def list_assets(tenant_id: UUID = Query(...), asset_type: str | None = None, session: Session = Depends(db)):
    stmt = select(models.NetworkAsset).where(models.NetworkAsset.tenant_id == tenant_id)
    if asset_type:
        stmt = stmt.where(models.NetworkAsset.asset_type == asset_type)
    return [{"id": str(a.id), "asset_type": a.asset_type, "name": a.name, "vendor_id": str(a.vendor_id) if a.vendor_id else None,
             "model": a.model, "serial_number": a.serial_number, "firmware_version": a.firmware_version,
             "site_owner": a.site_owner, "status": a.status} for a in session.scalars(stmt)]


@app.post("/api/oss/assets/{asset_id}/firmware", dependencies=[Depends(management_auth)])
def update_firmware(asset_id: UUID, payload: dict, session: Session = Depends(db)):
    try:
        log = AssetsService.update_firmware(session, UUID(str(payload.get("tenant_id"))),
                                            asset_id, payload.get("to_version"), by="operator")
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"asset_id": str(asset_id), "from": log.from_version, "to": log.to_version,
            "applied_at": log.applied_at}


@app.post("/api/oss/vendors", status_code=201, dependencies=[Depends(management_auth)])
def register_vendor(payload: dict, session: Session = Depends(db)):
    v = VendorService.register(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(v.id), "name": v.name, "sla_minutes": v.sla_minutes,
            "penalty_amount": v.penalty_amount, "status": v.status}


@app.get("/api/oss/vendors", dependencies=[Depends(management_auth)])
def list_vendors(tenant_id: UUID = Query(...), session: Session = Depends(db)):
    return [{"id": str(v.id), "name": v.name, "sla_minutes": v.sla_minutes,
             "penalty_amount": v.penalty_amount, "breaches": v.breaches,
             "performance_score": v.performance_score} for v in session.scalars(
        select(models.Vendor).where(models.Vendor.tenant_id == tenant_id))]


@app.post("/api/oss/vendors/{vendor_id}/evaluate", dependencies=[Depends(management_auth)])
def evaluate_vendor(vendor_id: UUID, payload: dict, session: Session = Depends(db)):
    try:
        v = VendorService.evaluate(session, UUID(str(payload.get("tenant_id"))), vendor_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(v.id), "performance_score": v.performance_score,
            "breaches": v.breaches, "penalty_amount": v.penalty_amount}


@app.post("/api/oss/splitters", status_code=201, dependencies=[Depends(management_auth)])
def add_splitter(payload: dict, session: Session = Depends(db)):
    try:
        s = AssetsService.add_splitter(session, UUID(str(payload.get("tenant_id"))), payload)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(s.id), "name": s.name, "level": s.level, "parent_id": str(s.parent_id) if s.parent_id else None}


@app.get("/api/oss/splitters/tree", dependencies=[Depends(management_auth)])
def splitter_tree(tenant_id: UUID = Query(...), session: Session = Depends(db)):
    return AssetsService.splitter_tree(session, tenant_id)


# ===========================================================================
# Config push + drift + inventory reconcile (Batch 3: 246, 248, 1013)
# ===========================================================================

@app.post("/api/oss/config/push", dependencies=[Depends(management_auth)])
def push_config(payload: dict, session: Session = Depends(db)):
    try:
        req = ConfigService.push(session, UUID(str(payload.get("tenant_id"))),
                                 UUID(str(payload.get("asset_id"))), payload.get("config"), by="operator")
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"request_id": str(req.id), "status": req.status, "asset_id": str(req.asset_id)}


@app.post("/api/oss/config/snapshot", dependencies=[Depends(management_auth)])
def config_snapshot(payload: dict, session: Session = Depends(db)):
    try:
        snap = ConfigService.snapshot(session, UUID(str(payload.get("tenant_id"))),
                                      UUID(str(payload.get("asset_id"))), payload.get("config"),
                                      baseline=bool(payload.get("baseline", False)))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"snapshot_id": str(snap.id), "config_hash": snap.config_hash, "baseline": snap.is_baseline}


@app.post("/api/oss/config/drift-check", dependencies=[Depends(management_auth)])
def drift_check(payload: dict, session: Session = Depends(db)):
    return {"drifted": ConfigService.detect_drift(session, UUID(str(payload.get("tenant_id"))))}


@app.post("/api/oss/inventory/reconcile", dependencies=[Depends(management_auth)])
def reconcile_inventory(payload: dict, session: Session = Depends(db)):
    return InventoryDriftService.reconcile(session, UUID(str(payload.get("tenant_id"))),
                                           payload.get("discovered", []))


# ===========================================================================
# Enterprise: SLA, VPN, bandwidth-on-demand (Batch 3: 673, 675, 676)
# ===========================================================================

@app.post("/api/oss/enterprise/slas", status_code=201, dependencies=[Depends(management_auth)])
def create_enterprise_sla(payload: dict, session: Session = Depends(db)):
    sla = EnterpriseService.create_sla(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(sla.id), "customer_id": sla.customer_id, "terms": sla.terms, "status": sla.status}


@app.post("/api/oss/enterprise/vpns", status_code=201, dependencies=[Depends(management_auth)])
def create_vpn(payload: dict, session: Session = Depends(db)):
    vpn = EnterpriseService.create_vpn(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(vpn.id), "name": vpn.name, "vpn_type": vpn.vpn_type, "status": vpn.status}


@app.post("/api/oss/enterprise/bandwidth", status_code=201, dependencies=[Depends(management_auth)])
def request_bandwidth(payload: dict, session: Session = Depends(db)):
    bod = EnterpriseService.request_bandwidth(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(bod.id), "subscription_id": bod.subscription_id, "boost_mbps": bod.boost_mbps,
            "expires_at": bod.expires_at}


# ===========================================================================
# Infra: CapEx, risk heatmap (Batch 3: 1143, 1462)
# ===========================================================================

@app.post("/api/oss/infra/capex", status_code=201, dependencies=[Depends(management_auth)])
def add_capex(payload: dict, session: Session = Depends(db)):
    rec = InfraService.add_capex(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(rec.id), "category": rec.category, "amount": rec.amount, "currency": rec.currency}


@app.post("/api/oss/infra/risk", status_code=201, dependencies=[Depends(management_auth)])
def assess_risk(payload: dict, session: Session = Depends(db)):
    risk = InfraService.assess_risk(session, UUID(str(payload.get("tenant_id"))),
                                    payload.get("scope"), payload.get("factors", {}))
    return {"id": str(risk.id), "scope": risk.scope, "risk_score": risk.risk_score, "level": risk.level}


@app.get("/api/oss/infra/risk-heatmap", dependencies=[Depends(management_auth)])
def risk_heatmap(tenant_id: UUID = Query(...), session: Session = Depends(db)):
    return InfraService.risk_heatmap(session, tenant_id)


# ===========================================================================
# Security + traffic (Batch 3: 1208, 1254)
# ===========================================================================

@app.post("/api/oss/security/ddos/check", dependencies=[Depends(management_auth)])
def ddos_check(payload: dict, session: Session = Depends(db)):
    attack = SecurityService.check_ddos(session, UUID(str(payload.get("tenant_id"))),
                                        payload.get("target"), payload.get("vector"),
                                        float(payload.get("volume_mbps", 0)),
                                        float(payload.get("baseline_mbps", 0)))
    if attack is None:
        return {"detected": False, "target": payload.get("target")}
    return {"detected": True, "attack_id": str(attack.id), "volume_mbps": attack.volume_mbps,
            "status": attack.status}


@app.get("/api/oss/security/ddos", dependencies=[Depends(management_auth)])
def list_ddos(tenant_id: UUID = Query(...), session: Session = Depends(db)):
    return [{"id": str(a.id), "target": a.target, "vector": a.vector,
             "volume_mbps": a.volume_mbps, "status": a.status, "started_at": a.started_at}
            for a in session.scalars(select(models.DDoSAttack)
                                     .where(models.DDoSAttack.tenant_id == tenant_id)
                                     .order_by(models.DDoSAttack.started_at.desc()))]


@app.post("/api/oss/security/ddos/{attack_id}/mitigate", dependencies=[Depends(management_auth)])
def mitigate_ddos(attack_id: UUID, payload: dict, session: Session = Depends(db)):
    try:
        a = SecurityService.mitigate(session, UUID(str(payload.get("tenant_id"))), attack_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(a.id), "status": a.status, "ended_at": a.ended_at}


@app.post("/api/oss/traffic/cost", status_code=201, dependencies=[Depends(management_auth)])
def record_traffic_cost(payload: dict, session: Session = Depends(db)):
    t = TrafficService.record_cost(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(t.id), "route": t.route, "volume_gb": t.volume_gb, "cost": t.cost}


@app.get("/api/oss/traffic/optimize", dependencies=[Depends(management_auth)])
def optimize_traffic(tenant_id: UUID = Query(...), session: Session = Depends(db)):
    return TrafficService.optimize(session, tenant_id)


# ===========================================================================
# Telemetry: IoT, MOS, room bandwidth, PMS (Batch 3: 707, 717, 722, 728)
# ===========================================================================

@app.post("/api/oss/telemetry/iot", status_code=201, dependencies=[Depends(management_auth)])
def ingest_iot(payload: dict, session: Session = Depends(db)):
    t = TelemetryService.ingest_iot(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(t.id), "device_id": t.device_id, "metric": t.metric, "value": t.value}


@app.post("/api/oss/telemetry/mos", status_code=201, dependencies=[Depends(management_auth)])
def record_mos(payload: dict, session: Session = Depends(db)):
    m = TelemetryService.record_mos(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(m.id), "session_id": m.session_id, "score": m.score}


@app.post("/api/oss/telemetry/rooms", dependencies=[Depends(management_auth)])
def set_room_bandwidth(payload: dict, session: Session = Depends(db)):
    r = TelemetryService.set_room_bandwidth(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(r.id), "room_number": r.room_number, "applied_mbps": r.applied_mbps}


@app.post("/api/oss/telemetry/properties", status_code=201, dependencies=[Depends(management_auth)])
def sync_property(payload: dict, session: Session = Depends(db)):
    p = TelemetryService.sync_property(session, UUID(str(payload.get("tenant_id"))), payload)
    return {"id": str(p.id), "property_name": p.property_name, "pms_system": p.pms_system,
            "status": p.status}

