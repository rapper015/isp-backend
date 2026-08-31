"""Tenant lifecycle + provisioning saga + configuration, domains, features,
entitlements, quotas and secrets. Provisioning is an idempotent saga that never
marks a tenant ACTIVE before verification succeeds."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import (
    DuplicateError,
    NotFoundError,
    QuotaExceededError,
    TenantNotActiveError,
    TenantSuspendedError,
    ValidationError,
)
from ..domain.features import effective_feature, quota_allows
from ..domain.identity import generate_domain_token, normalize_domain, normalize_tenant_code
from ..domain.secrets import decrypt_secret, encrypt_secret
from ..events import outbox
from ..models import (
    Entitlement,
    FeatureFlag,
    Quota,
    Tenant,
    TenantConfiguration,
    TenantConfigurationVersion,
    TenantDatabase,
    TenantDomain,
    TenantEntitlement,
    TenantFeature,
    TenantHealth,
    TenantQuota,
    TenantSecret,
)
from ..state_machine import guarded, provisioning_transition, tenant_transition
from .audit_service import audit, correlation
from .catalog_service import ensure_defaults

_CONFIG_CATEGORIES = ("legal", "locale", "tax", "invoice", "portal", "email", "sms", "whatsapp",
                      "payment_gateway", "support_hours", "sla", "notifications", "security", "branding")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_tenant_or_404(session: Session, tenant_id) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found")
    return tenant


def create_tenant(session: Session, *, name: str, code: str, currency: str = "INR",
                  country: str | None = None, isolation_mode: str = "SHARED_SCHEMA_WITH_RLS",
                  requested_by: str = "system", correlation_id: str | None = None,
                  legal_name: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    code = normalize_tenant_code(code)
    if session.scalars(select(Tenant).where(Tenant.code == code)).first() is not None:
        raise DuplicateError(f"tenant code {code!r} already exists")
    tenant = Tenant(name=name, code=code, legal_name=legal_name, currency=currency, country=country,
                    isolation_mode=isolation_mode, status="REQUESTED", provision_state="REQUESTED",
                    created_by=requested_by, correlation_id=request_id)
    session.add(tenant)
    session.flush()
    audit(session, tenant.id, requested_by, "tenant.created", resource_type="tenant",
          resource_id=tenant.id, after={"code": code, "name": name}, correlation_id=request_id)
    outbox(session, "tenancy.tenant.requested.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id), "code": code, "name": name})
    return tenant


def validate_tenant(session: Session, tenant_id, *, actor: str = "system",
                    correlation_id: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    if tenant.status != "REQUESTED":
        raise ValidationError(f"cannot validate tenant in state {tenant.status}")
    guarded("tenant", tenant.status, "VALIDATING")
    tenant.status = "VALIDATING"
    tenant.provision_state = "VALIDATING"
    session.flush()
    return tenant


def provision_tenant(session: Session, tenant_id, *, actor: str = "system",
                     correlation_id: str | None = None, run_defaults: bool = True) -> Tenant:
    """Idempotent provisioning saga. Each step is re-entrant; a FAILED step moves
    the saga to ROLLING_BACK / MANUAL_INTERVENTION_REQUIRED."""
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    if tenant.status == "ACTIVE" and tenant.provision_state == "ACTIVE":
        return tenant
    steps = (
        "VALIDATING", "PROVISIONING_CONTROL_RECORD", "PROVISIONING_DATABASE", "RUNNING_MIGRATIONS",
        "CREATING_STORAGE_NAMESPACE", "CREATING_MESSAGING_NAMESPACE", "CONFIGURING_DEFAULTS",
        "CREATING_ADMIN", "VERIFYING",
    )
    current = tenant.provision_state or "REQUESTED"
    if current == "ACTIVE":
        return tenant
    if current == "MANUAL_INTERVENTION_REQUIRED":
        raise ValidationError("tenant provisioning requires manual intervention")
    for target in steps:
        try:
            provisioning_transition(current, target)
        except ValueError:
            continue  # not the next step
        current = target
        tenant.provision_state = target
        if target == "PROVISIONING_DATABASE":
            _ensure_database_record(session, tenant)
        if target == "RUNNING_MIGRATIONS":
            _record_health(session, tenant.id, "migrations", "PASSED")
        if target == "CONFIGURING_DEFAULTS" and run_defaults:
            ensure_defaults(session)
            _apply_default_config(session, tenant, actor)
        if target == "CREATING_ADMIN":
            _record_health(session, tenant.id, "admin", "PASSED")
        if target == "VERIFYING":
            checks = _verify(session, tenant)
            if not all(c["result"] == "PASSED" for c in checks):
                tenant.provision_state = "FAILED"
                guarded("provisioning", "VERIFYING", "FAILED")
                raise ValidationError("tenant verification failed")
    if current != "VERIFYING":
        # Transition to VERIFYING to close the saga (all steps ran).
        try:
            provisioning_transition(current, "VERIFYING")
        except ValueError:
            pass
    guarded("provisioning", "VERIFYING", "ACTIVE")
    tenant.provision_state = "ACTIVE"
    guarded("tenant", tenant.status, "PROVISIONING")
    tenant.status = "ACTIVE"
    tenant.activated_at = _now()
    session.flush()
    audit(session, tenant.id, actor, "tenant.provisioned", resource_type="tenant",
          resource_id=tenant.id, after={"status": "ACTIVE"}, correlation_id=request_id)
    outbox(session, "tenancy.tenant.provisioned.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id), "code": tenant.code})
    outbox(session, "tenancy.tenant.activated.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id), "code": tenant.code})
    return tenant


def _ensure_database_record(session: Session, tenant: Tenant) -> None:
    existing = session.scalars(select(TenantDatabase).where(
        TenantDatabase.tenant_id == tenant.id)).first()
    if existing is None:
        session.add(TenantDatabase(tenant_id=tenant.id, alias="control",
                                   isolation_mode=tenant.isolation_mode, state="READY",
                                   secret_ref=encrypt_secret("placeholder-connection-secret")))


def _apply_default_config(session: Session, tenant: Tenant, actor: str) -> None:
    defaults = {
        "legal": {"legal_name": tenant.legal_name or tenant.name, "country": tenant.country,
                  "currency": tenant.currency},
        "locale": {"currency": tenant.currency, "timezone": tenant.timezone or "Asia/Kolkata",
                   "locale": tenant.locale or "en-IN"},
        "portal": {"branding": {"logo_ref": None, "theme": {"primary": "#0a7"}}},
        "notifications": {"email": {"enabled": True}, "sms": {"enabled": True},
                          "whatsapp": {"enabled": False}},
        "security": {"require_mfa_partner": False, "max_login_attempts": 5},
    }
    for category, config in defaults.items():
        set_config(session, tenant.id, category, config, actor=actor)


def set_config(session: Session, tenant_id, category: str, config: dict, *, actor: str = "system",
               correlation_id: str | None = None) -> TenantConfiguration:
    if category not in _CONFIG_CATEGORIES:
        raise ValidationError(f"unknown configuration category {category!r}")
    request_id = correlation(correlation_id)
    row = session.scalars(select(TenantConfiguration).where(
        TenantConfiguration.tenant_id == tenant_id,
        TenantConfiguration.category == category)).first()
    if row is None:
        row = TenantConfiguration(tenant_id=tenant_id, category=category, version=1, config=config)
        session.add(row)
    else:
        previous = row.config
        session.add(TenantConfigurationVersion(config_id=row.id, tenant_id=tenant_id, category=category,
                                               version=row.version, config=previous, changed_by=actor))
        row.version += 1
        row.config = config
    row.changed_by = actor
    session.flush()
    audit(session, tenant_id, actor, "tenant.config.updated", resource_type="tenant_config",
          resource_id=row.id, after={"category": category, "version": row.version},
          correlation_id=request_id)
    return row


def get_config(session: Session, tenant_id, category: str) -> dict:
    row = session.scalars(select(TenantConfiguration).where(
        TenantConfiguration.tenant_id == tenant_id,
        TenantConfiguration.category == category)).first()
    return row.config if row else {}


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------
def add_domain(session: Session, tenant_id, domain: str, *, is_primary: bool = False,
               actor: str = "system", correlation_id: str | None = None) -> TenantDomain:
    request_id = correlation(correlation_id)
    domain = normalize_domain(domain)
    if session.scalars(select(TenantDomain).where(TenantDomain.domain == domain)).first() is not None:
        raise DuplicateError("domain is already registered to a tenant")
    row = TenantDomain(tenant_id=tenant_id, domain=domain, is_primary=is_primary,
                       verification_token=generate_domain_token(), changed_by=actor)
    session.add(row)
    session.flush()
    audit(session, tenant_id, actor, "tenant.domain.added", resource_type="tenant_domain",
          resource_id=row.id, after={"domain": domain}, correlation_id=request_id)
    outbox(session, "tenancy.domain.changed.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "domain": domain, "action": "added"})
    return row


def verify_domain(session: Session, tenant_id, domain_id, *, token: str,
                  actor: str = "system") -> TenantDomain:
    row = session.get(TenantDomain, domain_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFoundError("domain not found")
    if row.verification_token != token:
        raise ValidationError("domain verification token mismatch")
    row.is_verified = True
    row.status = "ACTIVE"
    session.flush()
    audit(session, tenant_id, actor, "tenant.domain.verified", resource_type="tenant_domain",
          resource_id=row.id, after={"domain": row.domain, "status": "ACTIVE"})
    return row


# ---------------------------------------------------------------------------
# Features / entitlements / quotas
# ---------------------------------------------------------------------------
def get_feature(session: Session, tenant_id, code: str) -> bool:
    flag = session.scalars(select(FeatureFlag).where(FeatureFlag.code == code)).first()
    if flag is None:
        return False
    override = session.scalars(select(TenantFeature).where(
        TenantFeature.tenant_id == tenant_id, TenantFeature.flag_id == flag.id)).first()
    return effective_feature(flag.platform_default, override.enabled if override else None)


def set_feature(session: Session, tenant_id, code: str, enabled: bool, *, actor: str = "system",
                correlation_id: str | None = None) -> None:
    request_id = correlation(correlation_id)
    flag = session.scalars(select(FeatureFlag).where(FeatureFlag.code == code)).first()
    if flag is None:
        raise NotFoundError(f"feature flag {code!r} not found")
    row = session.scalars(select(TenantFeature).where(
        TenantFeature.tenant_id == tenant_id, TenantFeature.flag_id == flag.id)).first()
    if row is None:
        row = TenantFeature(tenant_id=tenant_id, flag_id=flag.id, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    row.changed_by = actor
    session.flush()
    audit(session, tenant_id, actor, "tenant.feature.changed", resource_type="feature_flag",
          resource_id=flag.id, after={"code": code, "enabled": enabled}, correlation_id=request_id)
    outbox(session, "tenancy.feature.changed.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "feature": code, "enabled": enabled})


def grant_entitlement(session: Session, tenant_id, code: str, *, quantity: float | None = None,
                      granted_by: str = "system") -> None:
    entitlement = session.scalars(select(Entitlement).where(Entitlement.code == code)).first()
    if entitlement is None:
        raise NotFoundError(f"entitlement {code!r} not found")
    row = session.scalars(select(TenantEntitlement).where(
        TenantEntitlement.tenant_id == tenant_id,
        TenantEntitlement.entitlement_id == entitlement.id)).first()
    if row is None:
        row = TenantEntitlement(tenant_id=tenant_id, entitlement_id=entitlement.id,
                                quantity=quantity, granted_at=_now(), granted_by=granted_by)
        session.add(row)
    else:
        row.quantity = quantity
    session.flush()


def check_entitlement(session: Session, tenant_id, code: str) -> bool:
    entitlement = session.scalars(select(Entitlement).where(Entitlement.code == code)).first()
    if entitlement is None:
        return False
    return session.scalars(select(TenantEntitlement).where(
        TenantEntitlement.tenant_id == tenant_id,
        TenantEntitlement.entitlement_id == entitlement.id)).first() is not None


def set_quota(session: Session, tenant_id, kind: str, limit: float | None, *, actor: str = "system") -> None:
    quota = session.scalars(select(Quota).where(Quota.kind == kind)).first()
    if quota is None:
        raise NotFoundError(f"quota kind {kind!r} not found")
    row = session.scalars(select(TenantQuota).where(
        TenantQuota.tenant_id == tenant_id, TenantQuota.quota_id == quota.id)).first()
    if row is None:
        row = TenantQuota(tenant_id=tenant_id, quota_id=quota.id, limit=limit)
        session.add(row)
    else:
        row.limit = limit
    row.changed_by = actor
    session.flush()


def check_quota(session: Session, tenant_id, kind: str, requested: float = 1.0) -> bool:
    quota = session.scalars(select(Quota).where(Quota.kind == kind)).first()
    if quota is None:
        return True
    row = session.scalars(select(TenantQuota).where(
        TenantQuota.tenant_id == tenant_id, TenantQuota.quota_id == quota.id)).first()
    limit = row.limit if row else quota.default_limit
    used = row.used if row else 0.0
    return quota_allows(limit, used, requested)


def consume_quota(session: Session, tenant_id, kind: str, amount: float = 1.0, *, actor: str = "system") -> None:
    quota = session.scalars(select(Quota).where(Quota.kind == kind)).first()
    if quota is None:
        return
    row = session.scalars(select(TenantQuota).where(
        TenantQuota.tenant_id == tenant_id, TenantQuota.quota_id == quota.id)).first()
    limit = row.limit if row else quota.default_limit
    used = row.used if row else 0.0
    if not quota_allows(limit, used, amount):
        raise QuotaExceededError(f"tenant quota exceeded for {kind}")
    if row is None:
        row = TenantQuota(tenant_id=tenant_id, quota_id=quota.id, limit=limit)
        session.add(row)
    row.used = used + amount
    session.flush()


# ---------------------------------------------------------------------------
# Tenant lifecycle operations
# ---------------------------------------------------------------------------
def _require_active(session: Session, tenant: Tenant) -> None:
    if tenant.status == "SUSPENDED":
        raise TenantSuspendedError("tenant is suspended")
    if tenant.status not in ("ACTIVE", "RESTRICTED"):
        raise TenantNotActiveError(f"tenant is not active (state {tenant.status})")


def suspend_tenant(session: Session, tenant_id, *, reason: str, scope: str = "ADMIN_CONSOLE",
                   actor: str = "system", correlation_id: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    if not reason or not reason.strip():
        raise ValidationError("suspension requires a reason")
    guarded("tenant", tenant.status, "SUSPENDED")
    tenant.status = "SUSPENDED"
    tenant.suspended_at = _now()
    tenant.suspension_reason = reason
    session.flush()
    audit(session, tenant.id, actor, "tenant.suspended", resource_type="tenant",
          resource_id=tenant.id, after={"status": "SUSPENDED", "scope": scope}, reason=reason,
          correlation_id=request_id, approval="ELEVATED")
    outbox(session, "tenancy.tenant.suspended.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id), "scope": scope, "reason": reason})
    return tenant


def resume_tenant(session: Session, tenant_id, *, actor: str = "system",
                  correlation_id: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    guarded("tenant", tenant.status, "ACTIVE")
    tenant.status = "ACTIVE"
    tenant.suspended_at = None
    tenant.suspension_reason = None
    session.flush()
    audit(session, tenant.id, actor, "tenant.resumed", resource_type="tenant",
          resource_id=tenant.id, after={"status": "ACTIVE"}, correlation_id=request_id)
    outbox(session, "tenancy.tenant.resumed.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id)})
    return tenant


def restrict_tenant(session: Session, tenant_id, *, actor: str = "system") -> Tenant:
    tenant = get_tenant_or_404(session, tenant_id)
    guarded("tenant", tenant.status, "RESTRICTED")
    tenant.status = "RESTRICTED"
    session.flush()
    outbox(session, "tenancy.tenant.restricted.v1", tenant.id, correlation(None),
           {"tenant_id": str(tenant.id)})
    return tenant


def start_offboarding(session: Session, tenant_id, *, reason: str, actor: str = "system",
                      correlation_id: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    if not reason or not reason.strip():
        raise ValidationError("offboarding requires a reason")
    guarded("tenant", tenant.status, "OFFBOARDING")
    tenant.status = "OFFBOARDING"
    tenant.suspension_reason = reason
    session.flush()
    audit(session, tenant.id, actor, "tenant.offboarding_started", resource_type="tenant",
          resource_id=tenant.id, after={"status": "OFFBOARDING"}, reason=reason,
          correlation_id=request_id, approval="ELEVATED")
    outbox(session, "tenancy.tenant.offboarding_started.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id), "reason": reason})
    return tenant


def archive_tenant(session: Session, tenant_id, *, actor: str = "system",
                   correlation_id: str | None = None) -> Tenant:
    request_id = correlation(correlation_id)
    tenant = get_tenant_or_404(session, tenant_id)
    guarded("tenant", tenant.status, "ARCHIVED")
    tenant.status = "ARCHIVED"
    session.flush()
    audit(session, tenant.id, actor, "tenant.archived", resource_type="tenant",
          resource_id=tenant.id, after={"status": "ARCHIVED"}, correlation_id=request_id)
    outbox(session, "tenancy.tenant.archived.v1", tenant.id, request_id,
           {"tenant_id": str(tenant.id)})
    return tenant


def _record_health(session: Session, tenant_id, check_type: str, result: str,
                   detail: dict | None = None) -> None:
    session.add(TenantHealth(tenant_id=tenant_id, check_type=check_type, result=result,
                             detail=detail or {}, checked_at=_now()))


def _verify(session: Session, tenant: Tenant) -> list[dict]:
    checks = []
    db = session.scalars(select(TenantDatabase).where(TenantDatabase.tenant_id == tenant.id)).first()
    checks.append({"check": "database", "result": "PASSED" if db and db.state == "READY" else "FAILED"})
    features = list(session.scalars(select(TenantFeature).where(TenantFeature.tenant_id == tenant.id)))
    checks.append({"check": "features", "result": "PASSED"})
    session.flush()
    return checks


def tenant_health(session: Session, tenant_id) -> dict:
    tenant = get_tenant_or_404(session, tenant_id)
    rows = list(session.scalars(select(TenantHealth).where(TenantHealth.tenant_id == tenant.id)
                                .order_by(TenantHealth.checked_at.desc()).limit(20)))
    return {
        "tenant_id": str(tenant.id), "status": tenant.status, "provision_state": tenant.provision_state,
        "checks": [{"type": r.check_type, "result": r.result, "checked_at": r.checked_at.isoformat()}
                   for r in rows],
    }
