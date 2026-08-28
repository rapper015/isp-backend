"""Pure reconciliation planner; the caller supplies a trusted RouterOS snapshot."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import ActiveSession

def reconcile_nas_sessions(session: Session, tenant_id, nas_id, router_session_ids: set[str]) -> dict[str, list[str]]:
    database = list(session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.nas_id == nas_id, ActiveSession.status.in_(["STARTING", "ACTIVE", "STALE"]))))
    database_ids = {item.session_id for item in database}
    return {"database_only": sorted(database_ids - router_session_ids), "router_only": sorted(router_session_ids - database_ids), "matching": sorted(database_ids & router_session_ids)}
