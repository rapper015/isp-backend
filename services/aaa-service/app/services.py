"""Business rules for synchronous decisions and durable accounting ingestion."""
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
import bcrypt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .models import AccountingEvent, ActiveSession, AuditLog, Credential, Nas, OutboxEvent, Tenant, UsageProjection
from .policy import calculate_policy
from .radius import safe_reply, traffic_counter

def correlation(value: str | None) -> str: return value or uuid4().hex
def outbox(session: Session, event_type: str, tenant_id, correlation_id: str, payload: dict, idempotency_key: str | None = None) -> None:
    session.add(OutboxEvent(event_type=event_type, tenant_id=tenant_id, correlation_id=correlation_id, idempotency_key=idempotency_key, payload=payload))
def audit(session: Session, tenant_id, action: str, target: str, correlation_id: str, detail: dict) -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor="internal-radius", action=action, target_type="aaa", target_id=target, correlation_id=correlation_id, detail=detail))

def resolve_nas(session: Session, attributes: dict) -> Nas | None:
    source_ip = attributes.get("NAS-IP-Address")
    if not source_ip: return None
    nas = session.scalar(select(Nas).where(Nas.source_ip == source_ip, Nas.enabled.is_(True)))
    if nas and attributes.get("NAS-Identifier") and nas.nas_identifier and nas.nas_identifier != attributes["NAS-Identifier"]: return None
    return nas

def authenticate(session: Session, attributes: dict, correlation_id: str) -> tuple[str, dict]:
    nas = resolve_nas(session, attributes)
    if not nas: return "REJECT_UNKNOWN_NAS", {}
    tenant = session.get(Tenant, nas.tenant_id)
    if not tenant or not tenant.enabled: return "REJECT_TENANT_DISABLED", {}
    service = attributes.get("Service-Type", "pppoe").casefold()
    method = "mschapv2" if "MS-CHAP-Password" in attributes else "chap" if "CHAP-Password" in attributes else "mac" if service == "mac" else "pap"
    if service not in nas.allowed_services: return "REJECT_SERVICE_NOT_ALLOWED", {}
    username = attributes.get("User-Name", "")
    credential = session.scalar(select(Credential).where(Credential.tenant_id == nas.tenant_id, Credential.username_normalized == username))
    if not credential: return "REJECT_UNKNOWN_SUBSCRIBER", {}
    if credential.status != "active": return "REJECT_ACCOUNT_DISABLED", {}
    if credential.expires_at and credential.expires_at < datetime.now(timezone.utc): return "REJECT_ACCOUNT_EXPIRED", {}
    if method not in credential.allowed_methods or method not in nas.allowed_methods: return "REJECT_METHOD_NOT_ALLOWED", {}
    calling_mac = attributes.get("Calling-Station-Id")
    if credential.mac_address and calling_mac != credential.mac_address: return "REJECT_MAC_MISMATCH", {}
    policy = calculate_policy({"tenant": tenant.policy.get("default_policy", {})})
    simultaneous_limit = policy.values.get("simultaneous_limit")
    if simultaneous_limit is not None:
        online = session.scalar(select(func.count()).select_from(ActiveSession).where(ActiveSession.tenant_id == nas.tenant_id, ActiveSession.subscriber_id == credential.subscriber_id, ActiveSession.status.in_(["STARTING", "ACTIVE", "STALE", "DISCONNECT_REQUESTED", "DISCONNECT_SENT"])))
        if online >= int(simultaneous_limit): return "REJECT_SIMULTANEOUS_LIMIT", {}
    quota_bytes = policy.values.get("data_quota_bytes")
    if quota_bytes is not None:
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        usage = session.scalar(select(UsageProjection).where(UsageProjection.tenant_id == nas.tenant_id, UsageProjection.subscriber_id == credential.subscriber_id, UsageProjection.period == period))
        if usage and (usage.input_octets + usage.output_octets) >= int(quota_bytes): return "REJECT_QUOTA_EXHAUSTED", {}
    # CHAP/MS-CHAP verification needs protocol-specific, recoverable material
    # and the complete challenge fields.  This REST contract intentionally
    # fails closed until a dedicated verifier is configured; it must never
    # pretend a bcrypt PAP hash can verify either protocol.
    if method in {"chap", "mschapv2"}: return "REJECT_METHOD_NOT_ALLOWED", {}
    if method == "mac":
        if credential.mac_address and calling_mac == credential.mac_address:
            password = "mac-bound"
        else:
            return "REJECT_MAC_MISMATCH", {}
    else:
        password = attributes.get("User-Password")
    if method != "mac" and (not password or not credential.password_hash or not bcrypt.checkpw(password.encode(), credential.password_hash.encode())): return "REJECT_INVALID_CREDENTIALS", {}
    nas.last_auth_at = datetime.now(timezone.utc)
    reply = policy.reply_attributes()
    outbox(session, "aaa.authentication.accepted.v1", nas.tenant_id, correlation_id, {"subscriber_id": str(credential.subscriber_id), "nas_id": str(nas.id), "decision": "ACCEPT"})
    audit(session, nas.tenant_id, "authentication.accepted", str(credential.subscriber_id), correlation_id, {"nas_id": str(nas.id)})
    return "ACCEPT", safe_reply(reply)

def authorize(session: Session, attributes: dict, correlation_id: str) -> tuple[str, dict]:
    nas = resolve_nas(session, attributes)
    if not nas: return "REJECT_UNKNOWN_NAS", {}
    credential = session.scalar(select(Credential).where(Credential.tenant_id == nas.tenant_id, Credential.username_normalized == attributes.get("User-Name", ""), Credential.status == "active"))
    if not credential: return "REJECT_UNKNOWN_SUBSCRIBER", {}
    tenant = session.get(Tenant, nas.tenant_id)
    if not tenant or not tenant.enabled: return "REJECT_TENANT_DISABLED", {}
    reply = calculate_policy({"tenant": tenant.policy.get("default_policy", {})}).reply_attributes()
    outbox(session, "aaa.authorization.calculated.v1", nas.tenant_id, correlation_id, {"subscriber_id": str(credential.subscriber_id), "attributes": reply})
    return "ACCEPT", reply

def accounting(session: Session, attributes: dict, diagnostic: dict, correlation_id: str, supplied_key: str | None) -> tuple[str, bool]:
    nas = resolve_nas(session, attributes)
    if not nas: return "REJECT_UNKNOWN_NAS", False
    event_type = attributes.get("Acct-Status-Type", "").casefold()
    allowed = {"start": "Start", "interim-update": "Interim-Update", "stop": "Stop", "accounting-on": "Accounting-On", "accounting-off": "Accounting-Off"}
    if event_type not in allowed: return "REJECT_POLICY", False
    session_id = attributes.get("Acct-Session-Id", "")
    if not session_id and event_type not in {"accounting-on", "accounting-off"}: return "REJECT_POLICY", False
    key = supplied_key or hashlib.sha256(f"{nas.id}:{session_id}:{event_type}:{attributes.get('Event-Timestamp','')}:{traffic_counter(attributes,'Input')}:{traffic_counter(attributes,'Output')}".encode()).hexdigest()
    credential = session.scalar(select(Credential).where(Credential.tenant_id == nas.tenant_id, Credential.username_normalized == attributes.get("User-Name", "")))
    now = datetime.now(timezone.utc)
    record = AccountingEvent(tenant_id=nas.tenant_id, nas_id=nas.id, subscriber_id=credential.subscriber_id if credential else None, idempotency_key=key, session_id=session_id or f"nas-{nas.id}", event_type=allowed[event_type], event_at=now, input_octets=traffic_counter(attributes, "Input"), output_octets=traffic_counter(attributes, "Output"), raw_redacted=diagnostic)
    try:
        session.add(record); session.flush()
    except IntegrityError:
        session.rollback(); return "DUPLICATE", True
    nas.last_accounting_at = now
    if session_id and event_type in {"start", "interim-update", "stop"}:
        active = session.scalar(select(ActiveSession).where(ActiveSession.tenant_id == nas.tenant_id, ActiveSession.session_id == session_id))
        if not active:
            active = ActiveSession(tenant_id=nas.tenant_id, nas_id=nas.id, subscriber_id=credential.subscriber_id if credential else None, username=attributes.get("User-Name", "unknown"), session_id=session_id, started_at=now, status="STARTING")
            session.add(active)
        incoming_in, incoming_out = record.input_octets, record.output_octets
        previous_in, previous_out = active.input_octets or 0, active.output_octets or 0
        active.input_octets, active.output_octets = max(previous_in, incoming_in), max(previous_out, incoming_out)
        active.last_interim_at = now; active.framed_ip = attributes.get("Framed-IP-Address", active.framed_ip)
        active.status = "STOPPED" if event_type == "stop" else "ACTIVE"
        active.termination_cause = attributes.get("Acct-Terminate-Cause") if event_type == "stop" else active.termination_cause
        if active.subscriber_id:
            period = now.strftime("%Y-%m")
            usage = session.scalar(select(UsageProjection).where(UsageProjection.tenant_id == nas.tenant_id, UsageProjection.subscriber_id == active.subscriber_id, UsageProjection.period == period))
            if not usage:
                usage = UsageProjection(tenant_id=nas.tenant_id, subscriber_id=active.subscriber_id, period=period)
                session.add(usage)
            usage.input_octets = (usage.input_octets or 0) + max(0, incoming_in - previous_in)
            usage.output_octets = (usage.output_octets or 0) + max(0, incoming_out - previous_out)
            tenant = session.get(Tenant, nas.tenant_id)
            fup_threshold = (tenant.policy.get("default_policy", {}) if tenant else {}).get("fup_threshold_bytes")
            if fup_threshold is not None and not usage.fup_active and usage.input_octets + usage.output_octets >= int(fup_threshold):
                usage.fup_active = True
                outbox(session, "aaa.fup.activated.v1", nas.tenant_id, correlation_id, {"subscriber_id": str(active.subscriber_id), "period": period})
    outbox(session, "aaa.accounting.received.v1", nas.tenant_id, correlation_id, {"accounting_event_id": str(record.id), "event_type": allowed[event_type]}, key)
    return "OK", True
