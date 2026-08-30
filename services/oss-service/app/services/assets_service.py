"""OSS asset, config, vendor, enterprise, infra, security services (Batch 3)."""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.events import publish_outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


def _tenant(session: Session, tenant_id: uuid.UUID):
    t = session.get(models.Tenant, tenant_id)
    if not t:
        t = models.Tenant(id=tenant_id, name=f"tenant-{tenant_id}", code=str(tenant_id)[:8])
        session.add(t)
        session.flush()
    return t


def _hash(config: str) -> str:
    return hashlib.sha256(config.encode()).hexdigest()


class AssetsService:
    @staticmethod
    def register_asset(session: Session, tenant_id, data: dict, by: str | None = None) -> models.NetworkAsset:
        _tenant(session, tenant_id)
        a = models.NetworkAsset(tenant_id=tenant_id, **_no_tenant(data))
        session.add(a)
        session.flush()
        publish_outbox(session, "oss.asset.registered.v1",
                       {"asset_id": str(a.id), "asset_type": a.asset_type, "name": a.name},
                       tenant_id=tenant_id)
        session.commit()
        return a

    @staticmethod
    def update_firmware(session: Session, tenant_id, asset_id: uuid.UUID, to_version: str,
                        by: str | None = None) -> models.FirmwareLog:
        asset = session.query(models.NetworkAsset).filter(
            models.NetworkAsset.id == asset_id,
            models.NetworkAsset.tenant_id == tenant_id).first()
        if not asset:
            raise KeyError("asset not found")
        log = models.FirmwareLog(tenant_id=tenant_id, asset_id=asset_id,
                                 from_version=asset.firmware_version, to_version=to_version,
                                 applied_by=by, applied_at=_now())
        asset.firmware_version = to_version
        session.add(log)
        session.flush()
        publish_outbox(session, "oss.asset.firmware_updated.v1",
                       {"asset_id": str(asset_id), "to_version": to_version},
                       tenant_id=tenant_id)
        session.commit()
        return log

    @staticmethod
    def add_splitter(session: Session, tenant_id, data: dict) -> models.SplitterNode:
        _tenant(session, tenant_id)
        parent_id = uuid.UUID(data.get("parent_id")) if data.get("parent_id") else None
        level = 1
        if parent_id:
            parent = session.query(models.SplitterNode).filter(
                models.SplitterNode.id == parent_id,
                models.SplitterNode.tenant_id == tenant_id).first()
            if not parent:
                raise KeyError("parent splitter not found")
            level = parent.level + 1
        s = models.SplitterNode(tenant_id=tenant_id, parent_id=parent_id, level=level,
                                **_no_tenant({k: v for k, v in data.items() if k != "parent_id"}))
        session.add(s)
        session.commit()
        return s

    @staticmethod
    def splitter_tree(session: Session, tenant_id) -> list[dict]:
        nodes = session.query(models.SplitterNode).filter(
            models.SplitterNode.tenant_id == tenant_id).all()
        by_id = {str(n.id): {"id": str(n.id), "name": n.name, "location": n.location,
                             "level": n.level, "ports_used": n.ports_used,
                             "ports_total": n.ports_total, "children": []} for n in nodes}
        roots = []
        for n in nodes:
            node = by_id[str(n.id)]
            if n.parent_id and str(n.parent_id) in by_id:
                by_id[str(n.parent_id)]["children"].append(node)
            else:
                roots.append(node)
        return roots


class VendorService:
    @staticmethod
    def register(session: Session, tenant_id, data: dict) -> models.Vendor:
        _tenant(session, tenant_id)
        v = models.Vendor(tenant_id=tenant_id, **_no_tenant(data))
        session.add(v)
        session.commit()
        return v

    @staticmethod
    def evaluate(session: Session, tenant_id, vendor_id: uuid.UUID) -> models.Vendor:
        v = session.query(models.Vendor).filter(
            models.Vendor.id == vendor_id, models.Vendor.tenant_id == tenant_id).first()
        if not v:
            raise KeyError("vendor not found")
        # Breach rate from assets' last config snapshot drift within window.
        asset_count = session.query(func.count(models.NetworkAsset.id)).filter(
            models.NetworkAsset.tenant_id == tenant_id,
            models.NetworkAsset.vendor_id == vendor_id).scalar() or 0
        drift_count = session.query(models.ConfigSnapshot).join(
            models.NetworkAsset, models.NetworkAsset.id == models.ConfigSnapshot.asset_id).filter(
            models.NetworkAsset.tenant_id == tenant_id,
            models.NetworkAsset.vendor_id == vendor_id,
            models.ConfigSnapshot.drift.is_(True)).count()
        if asset_count:
            v.breaches = drift_count
            v.performance_score = round(max(0.0, 100 - drift_count * 10), 2)
            if v.breaches > 0:
                v.penalty_amount = round(v.breaches * 500.0, 2)
        v.last_reviewed_at = _now()
        session.flush()
        publish_outbox(session, "oss.vendor.evaluated.v1",
                       {"vendor_id": str(vendor_id), "score": v.performance_score,
                        "penalty": v.penalty_amount}, tenant_id=tenant_id)
        session.commit()
        return v


class ConfigService:
    @staticmethod
    def push(session: Session, tenant_id, asset_id: uuid.UUID, config: str,
             by: str | None = None) -> models.ConfigPushRequest:
        asset = session.query(models.NetworkAsset).filter(
            models.NetworkAsset.id == asset_id,
            models.NetworkAsset.tenant_id == tenant_id).first()
        if not asset:
            raise KeyError("asset not found")
        req = models.ConfigPushRequest(tenant_id=tenant_id, asset_id=asset_id,
                                       config_text=config, status="APPLIED",
                                       pushed_by=by, pushed_at=_now())
        asset.last_config_hash = _hash(config)
        session.add(req)
        session.flush()
        publish_outbox(session, "oss.config.pushed.v1",
                       {"asset_id": str(asset_id), "status": "APPLIED",
                        "config_hash": asset.last_config_hash}, tenant_id=tenant_id)
        session.commit()
        return req

    @staticmethod
    def snapshot(session: Session, tenant_id, asset_id: uuid.UUID, config: str,
                 baseline: bool = False) -> models.ConfigSnapshot:
        asset = session.query(models.NetworkAsset).filter(
            models.NetworkAsset.id == asset_id,
            models.NetworkAsset.tenant_id == tenant_id).first()
        if not asset:
            raise KeyError("asset not found")
        snap = models.ConfigSnapshot(tenant_id=tenant_id, asset_id=asset_id,
                                     config_text=config, config_hash=_hash(config),
                                     is_baseline=baseline, drift=False)
        session.add(snap)
        session.flush()
        publish_outbox(session, "oss.config.snapshot_captured.v1",
                       {"asset_id": str(asset_id), "hash": snap.config_hash,
                        "baseline": baseline}, tenant_id=tenant_id)
        session.commit()
        return snap

    @staticmethod
    def detect_drift(session: Session, tenant_id) -> list[dict]:
        findings = []
        assets = session.query(models.NetworkAsset).filter(
            models.NetworkAsset.tenant_id == tenant_id).all()
        for asset in assets:
            baseline = session.query(models.ConfigSnapshot).filter(
                models.ConfigSnapshot.asset_id == asset.id,
                models.ConfigSnapshot.is_baseline.is_(True)).order_by(
                models.ConfigSnapshot.captured_at.desc()).first()
            latest = session.query(models.ConfigSnapshot).filter(
                models.ConfigSnapshot.asset_id == asset.id,
                models.ConfigSnapshot.is_baseline.is_(False)).order_by(
                models.ConfigSnapshot.captured_at.desc()).first()
            drifted = bool(baseline and latest and baseline.config_hash != latest.config_hash)
            if latest and drifted:
                latest.drift = True
                findings.append({"asset_id": str(asset.id), "asset_name": asset.name,
                                 "baseline_hash": baseline.config_hash,
                                 "current_hash": latest.config_hash})
                publish_outbox(session, "oss.config.drift_detected.v1",
                               {"asset_id": str(asset.id), "asset_name": asset.name},
                               tenant_id=tenant_id)
        session.commit()
        return findings


class InventoryDriftService:
    @staticmethod
    def reconcile(session: Session, tenant_id, discovered: list[dict]) -> dict:
        """Compare discovered device state with the asset register (1013)."""
        mismatches, matches = [], 0
        for d in discovered:
            asset = session.query(models.NetworkAsset).filter(
                models.NetworkAsset.tenant_id == tenant_id,
                (models.NetworkAsset.serial_number == d.get("serial_number"))
                | (models.NetworkAsset.name == d.get("name"))).first()
            if not asset:
                mismatches.append({"name": d.get("name"), "reason": "UNKNOWN_DEVICE"})
                continue
            if d.get("firmware_version") and asset.firmware_version != d.get("firmware_version"):
                mismatches.append({"asset_id": str(asset.id), "reason": "FIRMWARE_MISMATCH",
                                   "expected": asset.firmware_version,
                                   "actual": d.get("firmware_version")})
            else:
                matches += 1
        if mismatches:
            publish_outbox(session, "oss.inventory.drift_detected.v1",
                           {"tenant_id": str(tenant_id), "mismatches": mismatches},
                           tenant_id=tenant_id)
        session.commit()
        return {"matched": matches, "mismatches": mismatches}


class EnterpriseService:
    @staticmethod
    def create_sla(session: Session, tenant_id, data: dict) -> models.EnterpriseSLA:
        _tenant(session, tenant_id)
        sla = models.EnterpriseSLA(tenant_id=tenant_id, **_no_tenant(data))
        session.add(sla)
        session.flush()
        publish_outbox(session, "oss.enterprise.sla_created.v1",
                       {"sla_id": str(sla.id), "customer_id": sla.customer_id},
                       tenant_id=tenant_id)
        session.commit()
        return sla

    @staticmethod
    def create_vpn(session: Session, tenant_id, data: dict) -> models.VPNService:
        _tenant(session, tenant_id)
        vpn = models.VPNService(tenant_id=tenant_id, **_no_tenant(data))
        session.add(vpn)
        session.flush()
        publish_outbox(session, "oss.enterprise.vpn_created.v1",
                       {"vpn_id": str(vpn.id), "name": vpn.name, "type": vpn.vpn_type},
                       tenant_id=tenant_id)
        session.commit()
        return vpn

    @staticmethod
    def request_bandwidth(session: Session, tenant_id, data: dict) -> models.BandwidthOnDemand:
        _tenant(session, tenant_id)
        bod = models.BandwidthOnDemand(
            tenant_id=tenant_id, expires_at=_now() + timedelta(minutes=data.get("duration_minutes", 60)),
            **_no_tenant(data))
        session.add(bod)
        session.flush()
        publish_outbox(session, "oss.enterprise.bandwidth_requested.v1",
                       {"subscription_id": bod.subscription_id, "boost_mbps": bod.boost_mbps},
                       tenant_id=tenant_id)
        session.commit()
        return bod


class InfraService:
    @staticmethod
    def add_capex(session: Session, tenant_id, data: dict) -> models.CapExRecord:
        _tenant(session, tenant_id)
        rec = models.CapExRecord(tenant_id=tenant_id, **_no_tenant(data))
        session.add(rec)
        session.commit()
        return rec

    @staticmethod
    def assess_risk(session: Session, tenant_id, scope: str, factors: dict) -> models.InfraRisk:
        _tenant(session, tenant_id)
        score = 0.0
        score += min(float(factors.get("fault_frequency", 0)) * 10, 50)
        score += min(float(factors.get("age_years", 0)) * 8, 30)
        score += 20 if factors.get("fiber_degraded") else 0
        score += 10 if factors.get("no_redudancy") else 0
        score = round(min(score, 100), 2)
        level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
        risk = models.InfraRisk(tenant_id=tenant_id, scope=scope, risk_score=score,
                                level=level, factors=factors)
        session.add(risk)
        session.flush()
        publish_outbox(session, "oss.infra.risk_assessed.v1",
                       {"scope": scope, "score": score, "level": level}, tenant_id=tenant_id)
        session.commit()
        return risk

    @staticmethod
    def risk_heatmap(session: Session, tenant_id) -> list[dict]:
        rows = session.query(models.InfraRisk).filter(
            models.InfraRisk.tenant_id == tenant_id).order_by(
            models.InfraRisk.risk_score.desc()).limit(100).all()
        return [{"scope": r.scope, "risk_score": r.risk_score, "level": r.level,
                 "assessed_at": r.assessed_at} for r in rows]


class SecurityService:
    @staticmethod
    def check_ddos(session: Session, tenant_id, target: str, vector: str | None,
                   volume_mbps: float, baseline_mbps: float) -> models.DDoSAttack | None:
        _tenant(session, tenant_id)
        # Heuristic: sustained volume > 5x baseline (>200 mbps floor) => attack.
        if volume_mbps >= max(200.0, baseline_mbps * 5):
            attack = models.DDoSAttack(tenant_id=tenant_id, target=target, vector=vector,
                                       volume_mbps=volume_mbps, status="OPEN")
            session.add(attack)
            session.flush()
            publish_outbox(session, "oss.security.ddos_detected.v1",
                           {"target": target, "volume_mbps": volume_mbps, "vector": vector},
                           tenant_id=tenant_id)
            session.commit()
            return attack
        return None

    @staticmethod
    def mitigate(session: Session, tenant_id, attack_id: uuid.UUID) -> models.DDoSAttack:
        a = session.query(models.DDoSAttack).filter(
            models.DDoSAttack.id == attack_id, models.DDoSAttack.tenant_id == tenant_id).first()
        if not a:
            raise KeyError("attack not found")
        a.status = "MITIGATED"
        a.ended_at = _now()
        session.commit()
        return a


class TrafficService:
    @staticmethod
    def record_cost(session: Session, tenant_id, data: dict) -> models.TrafficCost:
        _tenant(session, tenant_id)
        t = models.TrafficCost(tenant_id=tenant_id, **_no_tenant(data))
        session.add(t)
        session.commit()
        return t

    @staticmethod
    def optimize(session: Session, tenant_id) -> dict:
        """Cheapest route by cost-per-GB (1254)."""
        rows = session.query(models.TrafficCost).filter(
            models.TrafficCost.tenant_id == tenant_id).all()
        if not rows:
            return {"recommendation": "no traffic cost data"}
        ranked = sorted(rows, key=lambda r: (r.cost / r.volume_gb if r.volume_gb else 0))
        best = ranked[0]
        return {"recommended_route": best.route,
                "cost_per_gb": round(best.cost / best.volume_gb, 4) if best.volume_gb else 0,
                "ranking": [{"route": r.route, "cost_per_gb": round(r.cost / r.volume_gb, 4)
                             if r.volume_gb else 0} for r in ranked]}


class TelemetryService:
    @staticmethod
    def ingest_iot(session: Session, tenant_id, data: dict) -> models.IoTDeviceTelemetry:
        _tenant(session, tenant_id)
        t = models.IoTDeviceTelemetry(tenant_id=tenant_id, **_no_tenant(data))
        session.add(t)
        session.commit()
        return t

    @staticmethod
    def record_mos(session: Session, tenant_id, data: dict) -> models.MOSScore:
        _tenant(session, tenant_id)
        m = models.MOSScore(tenant_id=tenant_id, **_no_tenant(data))
        session.add(m)
        session.commit()
        return m

    @staticmethod
    def set_room_bandwidth(session: Session, tenant_id, data: dict) -> models.RoomBandwidth:
        _tenant(session, tenant_id)
        row = session.query(models.RoomBandwidth).filter(
            models.RoomBandwidth.tenant_id == tenant_id,
            models.RoomBandwidth.room_number == data["room_number"]).first()
        if row:
            row.plan_mbps = data.get("plan_mbps", row.plan_mbps)
            row.applied_mbps = data.get("applied_mbps", row.applied_mbps)
            row.property = data.get("property", row.property)
        else:
            row = models.RoomBandwidth(tenant_id=tenant_id, **_no_tenant(data))
            session.add(row)
        session.commit()
        return row

    @staticmethod
    def sync_property(session: Session, tenant_id, data: dict) -> models.PMSProperty:
        _tenant(session, tenant_id)
        p = models.PMSProperty(tenant_id=tenant_id, synced_at=_now(), **_no_tenant(data))
        session.add(p)
        session.commit()
        return p
