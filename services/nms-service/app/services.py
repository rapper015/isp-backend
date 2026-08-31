"""NMS operations services (Master Spec Batch 7c)."""
import difflib
import uuid

from sqlalchemy.orm import Session

from . import models
from .events import outbox


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


class OpsService:
    @staticmethod
    def create_escalation_policy(session: Session, tenant_id, data: dict) -> models.EscalationPolicy:
        row = models.EscalationPolicy(tenant_id=tenant_id, enabled=True, **_no_tenant(data))
        session.add(row)
        session.flush()
        outbox(session, "nms.escalation.policy_created.v1", tenant_id,
               {"policy_id": str(row.id), "name": row.name, "rule_json": row.rule_json})
        outbox(session, "nms.escalation.triggered.v1", tenant_id,
               {"policy_id": str(row.id), "name": row.name})
        session.commit()
        return row

    @staticmethod
    def save_snapshot(session: Session, tenant_id, device_id: str, label: str,
                      config_text: str) -> models.ConfigSnapshot:
        row = session.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.tenant_id == tenant_id,
            models.ConfigSnapshot.device_id == device_id,
            models.ConfigSnapshot.label == label).first()
        if row:
            row.config_text = config_text
        else:
            row = models.ConfigSnapshot(tenant_id=tenant_id, device_id=device_id,
                                        label=label, config_text=config_text)
            session.add(row)
        session.commit()
        return row

    @staticmethod
    def config_diff(session: Session, tenant_id, device_id: str) -> dict:
        """Config Diff Viewer (1082)."""
        baseline = session.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.tenant_id == tenant_id,
            models.ConfigSnapshot.device_id == device_id,
            models.ConfigSnapshot.label == "BASELINE").first()
        current = session.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.tenant_id == tenant_id,
            models.ConfigSnapshot.device_id == device_id,
            models.ConfigSnapshot.label == "CURRENT").first()
        if not baseline or not current:
            raise KeyError("baseline and current snapshots required")
        diff = list(difflib.unified_diff(
            baseline.config_text.splitlines(), current.config_text.splitlines(),
            fromfile="BASELINE", tofile="CURRENT", lineterm=""))
        if diff:
            outbox(session, "nms.config.diff_detected.v1", tenant_id,
                   {"device_id": device_id, "lines": len(diff)})
            outbox(session, "nms.config.diff.generated.v1", tenant_id,
                   {"device_id": device_id, "drift": True, "lines": len(diff)})
            session.commit()
        return {"device_id": device_id, "drift": bool(diff), "diff": diff}

    @staticmethod
    def set_approval_sla(session: Session, tenant_id, data: dict) -> models.ApprovalSla:
        row = models.ApprovalSla(tenant_id=tenant_id, overdue_count=0, **_no_tenant(data))
        session.add(row)
        session.flush()
        outbox(session, "nms.approval.tracked.v1", tenant_id,
               {"approval_type": row.approval_type, "sla_minutes": row.sla_minutes})
        session.commit()
        return row

    @staticmethod
    def record_overdue(session: Session, tenant_id, approval_type: str, minutes: int) -> models.ApprovalSla:
        """Approval SLA (1124): mark overdue against the configured limit."""
        row = session.query(models.ApprovalSla).filter(
            models.ApprovalSla.tenant_id == tenant_id,
            models.ApprovalSla.approval_type == approval_type).first()
        if not row:
            raise KeyError("approval SLA not configured")
        if minutes > row.sla_minutes:
            row.overdue_count += 1
            outbox(session, "nms.approval.sla_overdue.v1", tenant_id,
                   {"approval_type": approval_type, "minutes": minutes})
            outbox(session, "nms.approval.tracked.v1", tenant_id,
                   {"approval_type": approval_type, "overdue": True})
            session.commit()
        return row

    @staticmethod
    def set_cache_strategy(session: Session, tenant_id, data: dict) -> models.CacheStrategy:
        row = session.query(models.CacheStrategy).filter(
            models.CacheStrategy.tenant_id == tenant_id,
            models.CacheStrategy.cache_key == data["cache_key"]).first()
        if row:
            row.ttl_seconds = data.get("ttl_seconds", row.ttl_seconds)
            row.strategy = data.get("strategy", row.strategy)
        else:
            row = models.CacheStrategy(tenant_id=tenant_id, **_no_tenant(data))
            session.add(row)
        session.flush()
        outbox(session, "nms.cache.strategy_updated.v1", tenant_id,
               {"cache_key": row.cache_key, "ttl_seconds": row.ttl_seconds})
        outbox(session, "nms.cache.optimized.v1", tenant_id,
               {"cache_key": row.cache_key, "strategy": row.strategy})
        session.commit()
        return row

    @staticmethod
    def apply_degradation(session: Session, tenant_id, data: dict) -> models.DegradationRule:
        row = session.query(models.DegradationRule).filter(
            models.DegradationRule.tenant_id == tenant_id,
            models.DegradationRule.service == data["service"]).first()
        if row:
            row.degraded_mode = data.get("degraded_mode", row.degraded_mode)
            row.keep_alive_pct = data.get("keep_alive_pct", row.keep_alive_pct)
            row.enabled = data.get("enabled", row.enabled)
        else:
            row = models.DegradationRule(tenant_id=tenant_id, enabled=True, **_no_tenant(data))
            session.add(row)
        session.flush()
        outbox(session, "nms.degradation.rule_applied.v1", tenant_id,
               {"service": row.service, "mode": row.degraded_mode,
                "keep_alive_pct": row.keep_alive_pct})
        outbox(session, "nms.degradation.applied.v1", tenant_id,
               {"service": row.service, "mode": row.degraded_mode})
        session.commit()
        return row

    @staticmethod
    def protect_queue(session: Session, tenant_id, data: dict) -> models.QueueSaturation:
        """Queue Saturation Protection (1344): trigger backpressure when full."""
        row = session.query(models.QueueSaturation).filter(
            models.QueueSaturation.tenant_id == tenant_id,
            models.QueueSaturation.queue == data["queue"]).first()
        depth = int(data.get("depth", 0))
        max_depth = int(data.get("max_depth", row.max_depth if row else 1000))
        protected = depth >= max_depth
        if row:
            row.depth, row.max_depth, row.protected = depth, max_depth, protected
        else:
            row = models.QueueSaturation(tenant_id=tenant_id, queue=data["queue"], depth=depth,
                                         max_depth=max_depth, protected=protected)
            session.add(row)
        if protected:
            outbox(session, "nms.queue.saturation_protected.v1", tenant_id,
                   {"queue": row.queue, "depth": depth, "max_depth": max_depth})
            outbox(session, "nms.queue.protection.applied.v1", tenant_id,
                   {"queue": row.queue, "depth": depth, "max_depth": max_depth})
            session.commit()
        else:
            session.commit()
        return row

    @staticmethod
    def create_runbook(session: Session, tenant_id, data: dict) -> models.Runbook:
        """Runbook Automation (284): predefined incident workflows."""
        row = models.Runbook(tenant_id=tenant_id, executions=0, status="ACTIVE",
                             **_no_tenant(data))
        session.add(row)
        session.commit()
        return row

    @staticmethod
    def trigger_runbook(session: Session, tenant_id, runbook_id: uuid.UUID) -> models.Runbook:
        row = session.query(models.Runbook).filter(
            models.Runbook.id == runbook_id,
            models.Runbook.tenant_id == tenant_id).first()
        if not row:
            raise KeyError("Runbook not found")
        row.executions += 1
        session.flush()
        outbox(session, "nms.runbook.triggered.v1", tenant_id,
               {"runbook_id": str(row.id), "name": row.name, "trigger": row.trigger,
                "executions": row.executions})
        session.commit()
        return row

    @staticmethod
    def generate_heatmap(session: Session, tenant_id, data: dict) -> models.AnomalyHeatmap:
        """Anomaly Heatmaps (743): aggregate anomaly cells by scope."""
        period = data.get("period", "DAY")
        scope = data["scope"]
        row = session.query(models.AnomalyHeatmap).filter(
            models.AnomalyHeatmap.tenant_id == tenant_id,
            models.AnomalyHeatmap.period == period,
            models.AnomalyHeatmap.scope == scope).first()
        cells = data.get("cells") or []
        if row:
            row.cells = cells
            row.anomaly_count = data.get("anomaly_count", sum(int(c.get("count", 0)) for c in cells))
        else:
            row = models.AnomalyHeatmap(tenant_id=tenant_id, period=period, scope=scope,
                                        cells=cells,
                                        anomaly_count=data.get("anomaly_count", sum(int(c.get("count", 0)) for c in cells)))
            session.add(row)
        session.flush()
        outbox(session, "nms.anomaly.heatmap.generated.v1", tenant_id,
               {"scope": scope, "period": period, "cells": len(cells)})
        session.commit()
        return row
