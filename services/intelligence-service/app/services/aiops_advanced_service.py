"""Intelligence aiops-advanced services (Master Spec Batch 8h — aiops P1/P2).

Network/business digital twins, autonomous scaling + pricing, upsell, voice
assistant, sentiment response, and digital workforce automation. These record
insights/recommendations; they never mutate domain state directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (BusinessTwin, NetworkTwin, PricingChange, ScalingAction,
                      SentimentResponse, UpsellSuggestion, VoiceInteraction,
                      WorkforceTask)
from .audit_service import audit, outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


class AiopsAdvancedService:
    @staticmethod
    def create_network_twin(session: Session, tenant_id, data: dict,
                            actor="system") -> NetworkTwin:
        """Network Digital Twin (731)."""
        data = _no_tenant(data)
        row = session.scalar(select(NetworkTwin).where(
            NetworkTwin.tenant_id == tenant_id,
            NetworkTwin.twin_name == data.get("twin_name", "")))
        if row:
            row.topology, row.state = data.get("topology", {}), data.get("state", {})
        else:
            row = NetworkTwin(tenant_id=tenant_id, **data)
            session.add(row)
        session.flush()
        outbox(session, "ai.network.twin.created.v1", tenant_id, None,
               {"twin_name": row.twin_name})
        audit(session, tenant_id, actor, "aiops.network_twin.create",
              resource_type="NetworkTwin", resource_id=row.id,
              after={"twin_name": row.twin_name})
        session.commit()
        return row

    @staticmethod
    def autonomous_scale(session: Session, tenant_id, data: dict,
                         actor="system") -> ScalingAction:
        """Autonomous Scaling (739)."""
        data = _no_tenant(data)
        row = ScalingAction(tenant_id=tenant_id, **data)
        session.add(row)
        session.flush()
        outbox(session, "ai.scaling.optimized.v1", tenant_id, None,
               {"service": row.service, "action": row.action, "reason": row.reason})
        audit(session, tenant_id, actor, "aiops.autoscale",
              resource_type="ScalingAction", resource_id=row.id,
              after={"service": row.service, "action": row.action})
        session.commit()
        return row

    @staticmethod
    def change_price(session: Session, tenant_id, data: dict, actor="system") -> PricingChange:
        """Autonomous Pricing (861)."""
        data = _no_tenant(data)
        row = PricingChange(tenant_id=tenant_id, **data)
        session.add(row)
        session.flush()
        outbox(session, "ai.pricing.changed.v1", tenant_id, None,
               {"product": row.product, "old_price": row.old_price,
                "new_price": row.new_price, "reason": row.reason})
        audit(session, tenant_id, actor, "aiops.pricing.change",
              resource_type="PricingChange", resource_id=row.id,
              after={"product": row.product})
        session.commit()
        return row

    @staticmethod
    def create_business_twin(session: Session, tenant_id, data: dict,
                             actor="system") -> BusinessTwin:
        """Business Digital Twin (871)."""
        data = _no_tenant(data)
        row = session.scalar(select(BusinessTwin).where(
            BusinessTwin.tenant_id == tenant_id,
            BusinessTwin.twin_name == data.get("twin_name", "")))
        if row:
            row.scenario, row.metrics = data.get("scenario", row.scenario), data.get("metrics", {})
        else:
            row = BusinessTwin(tenant_id=tenant_id, **data)
            session.add(row)
        session.flush()
        outbox(session, "ai.business.twin.created.v1", tenant_id, None,
               {"twin_name": row.twin_name, "scenario": row.scenario})
        session.commit()
        return row

    @staticmethod
    def suggest_upsell(session: Session, tenant_id, data: dict, actor="system") -> UpsellSuggestion:
        """Upsell Engine (883)."""
        data = _no_tenant(data)
        row = UpsellSuggestion(tenant_id=tenant_id, **data)
        session.add(row)
        session.flush()
        outbox(session, "ai.upsell.suggested.v1", tenant_id, None,
               {"customer_id": row.customer_id, "product": row.product})
        session.commit()
        return row

    @staticmethod
    def voice_respond(session: Session, tenant_id, data: dict, actor="system") -> VoiceInteraction:
        """Voice Assistant (886)."""
        query = data.get("query", "").lower()
        if "pay" in query or "payment" in query:
            response = "I can guide you through payment. Please say 'make payment'."
        elif "bill" in query or "invoice" in query:
            response = "I can help you check your bill. Please say your account number."
        elif "internet" in query or "slow" in query:
            response = "I can run a connectivity test. Please say 'run test'."
        else:
            response = "I can help with billing, support, and troubleshooting."
        row = VoiceInteraction(tenant_id=tenant_id, query=data.get("query", ""),
                               response=response)
        session.add(row)
        session.flush()
        outbox(session, "ai.voice.responded.v1", tenant_id, None,
               {"response": response})
        session.commit()
        return row

    @staticmethod
    def handle_sentiment(session: Session, tenant_id, data: dict, actor="system") -> SentimentResponse:
        """Sentiment Response (888)."""
        sentiment = data.get("sentiment", "NEUTRAL")
        action = data.get("action")
        if not action:
            action = {"POSITIVE": "Escalate to loyalty rewards",
                      "NEGATIVE": "Prioritize complaint handling and offer credit",
                      "NEUTRAL": "Log and continue standard flow"}.get(sentiment, "Log and continue")
        row = SentimentResponse(tenant_id=tenant_id, sentiment=sentiment, action=action)
        session.add(row)
        session.flush()
        outbox(session, "ai.sentiment.handled.v1", tenant_id, None,
               {"sentiment": sentiment, "action": action})
        session.commit()
        return row

    @staticmethod
    def automate_workforce(session: Session, tenant_id, data: dict, actor="system") -> WorkforceTask:
        """Digital Workforce (898)."""
        data = _no_tenant(data)
        row = WorkforceTask(tenant_id=tenant_id, status="AUTOMATED", **data)
        session.add(row)
        session.flush()
        outbox(session, "ai.workforce.replaced.v1", tenant_id, None,
               {"task_name": row.task_name, "automation_pct": row.automation_pct})
        session.commit()
        return row
