"""Idempotent scheduled AAA maintenance tasks; an external scheduler invokes these."""
from datetime import datetime, timedelta, timezone
from os import getenv
from sqlalchemy import select
from sqlalchemy.orm import Session
from .events import declare_topology, publish_outbox
from .commands import RadiusCommandAdapter
from .models import ActiveSession, Nas, RadiusCommand, RadiusServer
from .security import decrypt_secret
from .services import correlation, outbox

def detect_stale_sessions(session: Session, stale_after_seconds: int | None = None) -> int:
    seconds = stale_after_seconds or int(getenv("AAA_SESSION_STALE_SECONDS", "900"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    sessions = list(session.scalars(select(ActiveSession).where(ActiveSession.status == "ACTIVE", ActiveSession.last_interim_at.is_not(None), ActiveSession.last_interim_at < cutoff)))
    for item in sessions:
        item.status = "STALE"
        outbox(session, "aaa.session.stale.v1", item.tenant_id, correlation(None), {"session_id": str(item.id), "nas_id": str(item.nas_id)})
    session.commit()
    return len(sessions)

def evaluate_radius_server_health(session: Session, heartbeat_after_seconds: int | None = None) -> int:
    seconds = heartbeat_after_seconds or int(getenv("AAA_RADIUS_HEARTBEAT_SECONDS", "120"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    changed = 0
    for item in session.scalars(select(RadiusServer).where(RadiusServer.enabled.is_(True))):
        health = "healthy" if item.last_heartbeat_at and item.last_heartbeat_at >= cutoff else "unhealthy"
        if item.health != health:
            item.health = health; changed += 1
            outbox(session, "aaa.radius_server.health_changed.v1", None, correlation(None), {"radius_server_id": str(item.id), "health": health})
    session.commit()
    return changed

def flush_outbox(session: Session) -> int:
    return publish_outbox(session, limit=int(getenv("AAA_OUTBOX_BATCH_SIZE", "100")))

def ensure_event_topology() -> None:
    import asyncio
    asyncio.run(declare_topology())

def process_radius_command(session: Session, adapter: RadiusCommandAdapter) -> str | None:
    """Execute one queued command. A queue acknowledgement is never a session stop."""
    maximum_attempts = int(getenv("AAA_COMMAND_MAX_ATTEMPTS", "3"))
    command = session.scalar(select(RadiusCommand).where(RadiusCommand.status == "QUEUED", RadiusCommand.attempts < maximum_attempts).order_by(RadiusCommand.created_at).limit(1))
    if not command: return None
    command.status = "SENDING"; command.attempts += 1; session.commit()
    nas = session.get(Nas, command.nas_id)
    if not nas or not nas.enabled or not nas.secret_ciphertext:
        result = None
    else:
        try:
            secret = decrypt_secret(nas.secret_ciphertext)
            result = adapter.send_disconnect(nas.source_ip, nas.coa_port, secret, command.attributes) if command.command_type == "DISCONNECT" else adapter.send_coa(nas.source_ip, nas.coa_port, secret, command.attributes)
        except Exception:
            result = None
    status = result.status if result else "FAILED"
    command.status, command.result = status, {"detail": result.detail if result else "NAS secret or delivery unavailable"}
    event = "aaa.disconnect.completed.v1" if command.command_type == "DISCONNECT" and status == "ACKNOWLEDGED" else "aaa.coa.completed.v1" if command.command_type == "COA" and status == "ACKNOWLEDGED" else "aaa.disconnect.failed.v1" if command.command_type == "DISCONNECT" else "aaa.coa.failed.v1"
    outbox(session, event, command.tenant_id, command.correlation_id, {"command_id": str(command.id), "status": status}, command.idempotency_key)
    if command.session_id:
        active = session.get(ActiveSession, command.session_id)
        if active:
            active.status = "DISCONNECT_ACKNOWLEDGED" if command.command_type == "DISCONNECT" and status == "ACKNOWLEDGED" else "DISCONNECT_TIMED_OUT" if status == "TIMED_OUT" else active.status
    session.commit()
    return status
