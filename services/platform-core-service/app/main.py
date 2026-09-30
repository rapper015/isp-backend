"""Platform operator authentication. It never authenticates RADIUS subscribers."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from os import getenv
import json
from urllib import error as urlerror, request as urlrequest
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from isp_shared.cors import cors_allowed_origins
from .database import SessionLocal
from .models import AccessTemplate, AccessTemplatePermission, Department, OrganizationAccessTemplate, Permission, PlatformUser, RefreshToken, Role, RolePermission, SecurityAuditEvent, ServiceAccount, UserAccessScope, UserRole
from .schemas import AccessTemplateAssignmentIn, AccessTemplateCreateIn, AccessTemplateUpdateIn, AdminPasswordResetIn, DepartmentCreateIn, DepartmentUpdateIn, LoginIn, PasswordChangeIn, RefreshIn, RoleCreateIn, RoleUpdateIn, ServiceAccountCreateIn, UserCreateIn, UserUpdateIn
from .security import bearer_claims, hash_password, issue_access_token, issue_service_access_token, limited, new_refresh_token, token_hash, verify_password

MENU_PERMISSIONS = {
    "menu.dashboard.view": ("Workspace", "Dashboard"),
    "menu.leads.view": ("Customer management", "Leads"),
    "menu.customers.view": ("Customer management", "Users"),
    "menu.followups.view": ("Customer management", "Follow-ups"),
    "menu.tenants.view": ("Organization", "Tenants"),
    "menu.franchises.view": ("Organization", "Franchises"),
    "menu.branches.view": ("Organization", "Branches"),
    "menu.orders.view": ("Service delivery", "Provisioning orders"),
    "menu.subscriptions.view": ("Service delivery", "Service subscriptions"),
    "menu.plans.view": ("Service delivery", "Service plans"),
    "menu.interventions.view": ("Service delivery", "Manual interventions"),
    "menu.resources.view": ("Service delivery", "Network resources"),
    "menu.invoices.view": ("Billing and revenue", "Invoices"),
    "menu.payments.view": ("Billing and revenue", "Payments"),
    "menu.network.view": ("Network and AAA", "Network control"),
    "menu.sessions.view": ("Network and AAA", "Sessions"),
    "menu.nas.view": ("Network and AAA", "NAS and MikroTik"),
    "menu.radius.view": ("Network and AAA", "RADIUS infrastructure"),
    "menu.policies.view": ("Network and AAA", "Policy versions"),
    "menu.control_actions.view": ("Network and AAA", "Control actions"),
    "menu.routeros.view": ("Network and AAA", "RouterOS configuration"),
    "menu.fup.view": ("Network and AAA", "FUP usage"),
    "menu.qos.view": ("Network and AAA", "Bandwidth and QoS"),
    "menu.ip_identity.view": ("Network and AAA", "IP identity"),
    "menu.subscriber_policies.view": ("Network and AAA", "Subscriber policies"),
    "menu.aaa_admin.view": ("Network and AAA", "AAA administration"),
    "menu.reports.view": ("Insights", "Reports"),
    "menu.departments.view": ("Administration", "Departments"),
    "menu.roles.view": ("Administration", "Roles and permissions"),
    "menu.admin_accounts.view": ("Administration", "Administrator accounts"),
    "menu.settings.view": ("Administration", "System settings"),
    "menu.access_templates.view": ("Administration", "Access templates"),
}

# Every operator-facing permission enforced by a public management API belongs
# in this catalog. Internal service credentials are intentionally excluded.
SERVICE_PERMISSIONS = {
    # Tenancy, governance and partner operations.
    "domains.manage", "config.manage", "entitlements.manage", "quota.manage",
    "org.units.manage", "partners.create", "partners.manage", "partners.view",
    "agreements.manage", "agreements.approve", "ownership.manage", "ownership.transfer",
    "customers.view", "customers.create", "customers.own.view", "grants.manage",
    "memberships.manage", "roles.manage", "permissions.manage", "access.review",
    "service_accounts.manage", "impersonate", "commissions.manage", "commissions.calculate",
    "commissions.plan.approve", "settlements.manage", "settlements.calculate",
    "settlements.approve", "settlements.reversal", "payouts.record", "payouts.reconcile",
    "wallet.adjust", "reports.view", "reports.export", "reports.aggregate", "audit.view",
    "governance.view", "governance.manage",

    # AAA and network operations.
    "aaa.accounting.view", "aaa.accounting.replay", "aaa.audit.view",
    "aaa.control.view", "aaa.control.manage", "aaa.fup.view", "aaa.fup.manage",
    "aaa.ip.view", "aaa.ip.regulatory_lookup", "aaa.radius_server.view", "aaa.radius_server.manage",
    "aaa.reconcile.run", "aaa.router.readiness", "aaa.router.manage",
    "aaa.session.view", "aaa.session.disconnect", "aaa.session.coa",
    "aaa.session.force_reauth", "aaa.session.reapply", "aaa.subscriber_policy.view",
    "aaa.subscriber_policy.manage", "aaa.usage.view",

    # Network monitoring.
    "nms.view", "nms.ops.view", "nms.ops.manage",

    # Customer support and service desk.
    "support.ticket.view", "support.ticket.create", "support.ticket.assign",
    "support.ticket.transfer", "support.ticket.resolve", "support.ticket.close",
    "support.ticket.reopen", "support.ticket.cancel", "support.ticket.escalate",
    "support.ticket.public_reply", "support.ticket.internal_note", "support.ticket.mark_duplicate",
    "support.diagnostic.view", "support.diagnostic.run", "support.outage.link",
    "support.action.request", "support.action.approve", "support.action.execute",
    "support.catalog.manage", "support.routing.manage", "support.sla.manage",
    "support.kb.manage", "support.billing.summary.view", "support.report.view",
    "support.audit.view", "support.export",

    # Field workforce.
    "dashboard.view", "technicians.view", "technicians.manage", "workorders.view",
    "workorders.manage", "dispatch.manage", "visits.manage", "shifts.view", "shifts.manage",
    "inventory.view", "inventory.manage", "inventory.consume", "proof.manage",
    "location.ingest", "fieldops.manage", "escalations.manage", "feedback.view",
    "sla.view", "kpi.view",

    # Assurance and NOC.
    "alerts.view", "alerts.ack", "alerts.manage", "incidents.view", "incidents.declare",
    "incidents.manage", "incidents.resolve", "slo.view", "slo.manage", "slo.approve",
    "kpi.manage", "maintenance.view", "maintenance.manage", "maintenance.approve", "root_cause.manage",
    "root_cause.confirm", "postmortem.manage", "postmortem.approve", "synthetic.view",
    "synthetic.manage", "dashboards.view", "dashboards.manage", "changes.view", "telemetry.ingest",

    # CPE and device lifecycle.
    "device.view", "device.view_parameters", "device.view_audit", "device.claim",
    "device.assign", "device.transfer", "device.change_parameter", "device.apply_profile",
    "device.profile.manage", "device.refresh", "device.reboot", "device.run_diagnostics",
    "device.action.execute", "device.bulk_action", "device.decommission", "device.export",
    "device.factory_reset", "device.firmware.upload", "device.firmware.approve",
    "device.firmware.approve_stage", "device.firmware.rollout", "device.firmware.execute",

    # Intelligence and automation.
    "ai.ops.view", "ai.ops.manage", "predictions.view", "recommendations.view",
    "fraud.view", "fraud.manage", "churn.view", "retention.manage", "features.view",
    "features.manage", "datasets.view", "datasets.manage", "models.view", "models.manage",
    "models.approve", "training.manage", "deploy.manage", "monitoring.view",
    "remediation.view", "remediation.manage", "remediation.execute", "kill_switch.manage",

    # Security, privacy and compliance.
    "events.view", "events.export", "events.ingest", "cases.view", "cases.manage", "cases.escalate",
    "cases.resolve", "violations.view", "violations.manage", "policies.manage",
    "vuln.manage", "consent.view", "consent.manage", "dsar.manage", "evidence.view",
    "li.approve", "audit.export",

    # Data warehouse and reporting.
    "analytics.view", "analytics.manage",
}

PERMISSIONS = {
    "platform.users.read", "platform.users.create", "platform.users.update", "platform.users.disable", "platform.roles.read", "platform.roles.manage",
    "platform.departments.read", "platform.departments.manage", "platform.access_templates.read", "platform.access_templates.manage",
    "tenants.view", "tenants.create", "tenants.manage", "tenants.activate", "tenants.suspend",
    "tenants.offboard", "tenants.export", "tenants.health",
    "aaa.nas.read", "aaa.nas.manage", "aaa.subscribers.read", "aaa.subscribers.manage", "aaa.sessions.read",
    "aaa.sessions.disconnect", "aaa.radius.manage",
    "aaa.ip_pool.view", "aaa.ip_pool.manage", "aaa.secret.manage",
    "aaa.policy.view", "aaa.policy.manage", "aaa.policy.explain",
    "crm.leads.read", "crm.leads.manage", "crm.customers.read", "crm.customers.manage", "crm.connections.manage",
    "crm.lead.view", "crm.lead.create", "crm.lead.assign", "crm.lead.transition", "crm.lead.convert",
    "crm.customer.view", "crm.customer.create", "crm.customer.update", "crm.customer.merge",
    "crm.customer.risk_override", "crm.customer.lifecycle_transition", "crm.followup.manage",
    "crm.kyc.view", "crm.kyc.submit", "crm.kyc.verify", "crm.kyc.reject", "crm.document.view_sensitive",
    "crm.franchise.view", "crm.franchise.manage", "crm.audit.view",
    "bss.plan.read", "bss.plan.manage",
    "bss.invoice.view", "bss.invoice.manage", "bss.payment.view", "bss.payment.manage", "bss.payment.capture",
    "bss.refund.approve", "bss.manual_payment.submit", "bss.manual_payment.approve", "bss.ledger.view",
    "bss.reconciliation.view", "bss.reconciliation.manage", "bss.dunning.view", "bss.dunning.manage",
    "bss.gateway.manage", "bss.webhook.view", "bss.report.view", "bss.audit.view",
    "oss.order.view", "oss.order.create", "oss.order.submit", "oss.order.transition", "oss.order.cancel",
    "oss.order.retry", "oss.order.resume", "oss.order.compensate", "oss.order.manual_resolve",
    "oss.resource.view", "oss.resource.manage", "oss.subscription.view", "oss.subscription.manage",
    "oss.workflow.view", "oss.audit.view", "oss.export", "oss.asset.view", "oss.asset.manage",
    "oss.config.manage", "oss.vendor.manage", "oss.enterprise.manage", "oss.infra.view", "oss.infra.manage",
    "oss.security.manage", "oss.telemetry.ingest",
} | SERVICE_PERMISSIONS | set(MENU_PERMISSIONS)
ROLE_PERMISSIONS = {"PLATFORM_SUPER_ADMIN": {"*"}, "TENANT_ADMIN": PERMISSIONS,
    "FRANCHISE_ADMIN": ({permission for permission in PERMISSIONS if not permission.startswith("platform.") and permission not in {"bss.plan.manage", "aaa.policy.manage", "aaa.ip_pool.manage", "aaa.secret.manage", "menu.access_templates.view"}}
        | {"platform.users.read", "platform.users.create", "platform.users.update", "platform.users.disable", "platform.roles.read", "platform.departments.read"}),
    "READ_ONLY": {"platform.users.read", "aaa.nas.read", "aaa.subscribers.read", "aaa.sessions.read", "aaa.ip_pool.view", "aaa.policy.view", "aaa.policy.explain", "crm.lead.view", "crm.customer.view", "crm.franchise.view", "bss.plan.read"}}

def utc(value):
    """Normalize SQLite's naive timestamps and PostgreSQL's aware timestamps."""
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()
def audit(session, action, user_id=None): session.add(SecurityAuditEvent(user_id=user_id, action=action))
def normalize_email(value): return value.strip().lower() if value else None
def normalize_mobile(value):
    if not value: return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None

def ensure_unique_user_identity(session, username, email=None, mobile=None):
    username_normalized = username.strip().lower()
    email_normalized = normalize_email(email)
    mobile_normalized = normalize_mobile(mobile)
    if session.scalar(select(PlatformUser.id).where(PlatformUser.username_normalized == username_normalized)):
        raise HTTPException(409, "username already exists")
    if email_normalized and session.scalar(select(PlatformUser.id).where(PlatformUser.email_normalized == email_normalized)):
        raise HTTPException(409, "email address already exists")
    if mobile_normalized and session.scalar(select(PlatformUser.id).where(PlatformUser.mobile_normalized == mobile_normalized)):
        raise HTTPException(409, "mobile number already exists")
    return username_normalized, email_normalized, mobile_normalized
def ensure_foundations(session):
    # The wildcard is a persisted permission granted only to the bootstrap role.
    # Runtime authorization therefore always comes from role-permission bindings,
    # never from a special role-name check.
    for name in sorted(PERMISSIONS | {"*"}):
        if not session.scalar(select(Permission).where(Permission.name == name)): session.add(Permission(name=name))
    session.flush()
    for name, names in ROLE_PERMISSIONS.items():
        role = session.scalar(select(Role).where(Role.name == name))
        if not role: role = Role(name=name, global_role=name == "PLATFORM_SUPER_ADMIN"); session.add(role); session.flush()
        existing = session.execute(
            select(RolePermission, Permission.name)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id == role.id)
        ).all()
        for binding, permission_name in existing:
            if permission_name not in names:
                session.delete(binding)
        for permission_name in names:
            permission = session.scalar(select(Permission).where(Permission.name == permission_name))
            if not session.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id)):
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))
def assign_roles(session, user, names):
    for name in names:
        role = session.scalar(select(Role).where(Role.name == name))
        if not role: raise HTTPException(422, "unknown platform role")
        if not session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id, UserRole.tenant_id == user.tenant_id)):
            session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=user.tenant_id))
def set_user_roles(session, user, role_ids, claims, department_id=None):
    existing = {item.role_id: item for item in session.scalars(select(UserRole).where(UserRole.user_id == user.id))}
    desired = set(role_ids)
    roles = {item.id: item for item in session.scalars(select(Role).where(Role.id.in_(desired), Role.enabled.is_(True)))} if desired else {}
    if set(roles) != desired: raise HTTPException(422, "one or more roles are invalid or inactive")
    if department_id and any(role.department_id != department_id for role in roles.values()): raise HTTPException(422, "all selected roles must belong to the selected department")
    if "*" not in claims["permissions"]:
        for role in roles.values():
            if role.scope != "TENANT" or str(role.tenant_id) != str(claims.get("tenant_id")): raise HTTPException(403, "role cannot be delegated outside your tenant")
            if not set(role_permissions(session, role.id)).issubset(set(claims["permissions"])): raise HTTPException(403, "role contains permissions you cannot delegate")
    for role_id, binding in existing.items():
        if role_id not in desired: session.delete(binding)
    for role_id in desired:
        if role_id not in existing: session.add(UserRole(user_id=user.id, role_id=role_id, tenant_id=user.tenant_id))
def user_scopes(session, user_id):
    return [{"scope_type": item.scope_type, "scope_id": str(item.scope_id) if item.scope_id else None}
            for item in session.scalars(select(UserAccessScope).where(UserAccessScope.user_id == user_id).order_by(UserAccessScope.scope_type, UserAccessScope.scope_id))]
def effective_access(scopes):
    """Expand stored leaf scopes into their read-only organization ancestry."""
    result = {"platform": False, "tenant_ids": set(), "franchise_ids": set(), "branch_ids": set()}
    base = getenv("PLATFORM_CRM_BASE_URL", "http://crm-service:8000").rstrip("/")
    key = getenv("PLATFORM_CRM_INTERNAL_API_KEY", "")
    def crm(path):
        request = urlrequest.Request(f"{base}/api/crm/{path}", headers={"X-CRM-Service-Key": key})
        try:
            with urlrequest.urlopen(request, timeout=5) as response: return json.loads(response.read())
        except (urlerror.URLError, ValueError) as error:
            raise HTTPException(503, "organization access could not be resolved") from error
    for scope in scopes:
        kind, identifier = scope["scope_type"], scope.get("scope_id")
        if kind == "PLATFORM": result["platform"] = True
        elif kind == "TENANT" and identifier: result["tenant_ids"].add(identifier)
        elif kind == "FRANCHISE" and identifier:
            item = crm(f"franchises/{identifier}")
            result["franchise_ids"].add(identifier); result["tenant_ids"].add(str(item["tenant_id"]))
        elif kind == "BRANCH" and identifier:
            item = crm(f"branches/{identifier}")
            result["branch_ids"].add(identifier); result["franchise_ids"].add(str(item["franchise_id"])); result["tenant_ids"].add(str(item["tenant_id"]))
    return {"platform": result["platform"], "tenant_ids": sorted(result["tenant_ids"]),
            "franchise_ids": sorted(result["franchise_ids"]), "branch_ids": sorted(result["branch_ids"])}
def set_user_scopes(session, user, scopes, actor_id):
    session.query(UserAccessScope).filter(UserAccessScope.user_id == user.id).delete(synchronize_session=False)
    for scope in scopes:
        session.add(UserAccessScope(user_id=user.id, scope_type=scope.scope_type, scope_id=scope.scope_id, created_by=actor_id))
def safe_user(session, user):
    roles = session.execute(select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).scalars().all()
    return {"id": str(user.id), "username": user.username, "email": user.email, "full_name": user.full_name,
            "mobile": user.mobile, "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "franchise_id": str(user.franchise_id) if user.franchise_id else None, "department_id": str(user.department_id) if user.department_id else None, "enabled": user.enabled,
            "is_organization_owner": user.is_organization_owner,
            "membership_level": "FRANCHISE" if user.franchise_id else "TENANT" if user.tenant_id else "PLATFORM",
            "last_login_at": user.last_login_at, "roles": [{"id": str(role.id), "name": role.display_name or role.name, "code": role.code or role.name} for role in roles],
            "access_scopes": user_scopes(session, user.id)}
def validate_delegated_scopes(claims, scopes):
    if "*" in claims["permissions"]: return
    tenant_id, franchise_id = claims.get("tenant_id"), claims.get("franchise_id")
    for scope in scopes:
        if scope.scope_type == "PLATFORM": raise HTTPException(403, "platform-wide access cannot be delegated")
        if scope.scope_type == "TENANT" and str(scope.scope_id) != str(tenant_id): raise HTTPException(403, "tenant access cannot be delegated")
        if franchise_id and scope.scope_type == "FRANCHISE" and str(scope.scope_id) != str(franchise_id): raise HTTPException(403, "franchise access cannot be delegated")
        if franchise_id and scope.scope_type not in {"FRANCHISE", "BRANCH"}: raise HTTPException(403, "franchise staff can only be assigned this franchise or its branches")
def claims_for(session, user):
    role_rows = session.execute(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).scalars().all()
    permissions = set(session.execute(select(Permission.name).join(RolePermission, RolePermission.permission_id == Permission.id).join(Role, Role.id == RolePermission.role_id).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).scalars())
    # Organization templates are permission ceilings. Roles grant capabilities;
    # each assigned tenant/franchise template can only reduce that grant.
    targets = []
    if user.tenant_id: targets.append(("TENANT", user.tenant_id))
    if user.franchise_id: targets.append(("FRANCHISE", user.franchise_id))
    for target_type, target_id in targets:
        template = session.scalar(
            select(AccessTemplate)
            .join(OrganizationAccessTemplate, OrganizationAccessTemplate.template_id == AccessTemplate.id)
            .where(OrganizationAccessTemplate.target_type == target_type,
                   OrganizationAccessTemplate.target_id == target_id)
        )
        # Organization access templates are mandatory entitlement boundaries.
        # Missing and disabled templates must fail closed; otherwise a newly
        # created tenant or franchise would inherit every permission granted by
        # its administrative role before a platform administrator configures it.
        ceiling = set(session.execute(
            select(Permission.name)
            .join(AccessTemplatePermission, AccessTemplatePermission.permission_id == Permission.id)
            .where(AccessTemplatePermission.template_id == template.id)
        ).scalars()) if template and template.enabled else set()
        permissions = ceiling if "*" in permissions else permissions & ceiling
    return role_rows, permissions
def tokens(session, user):
    roles, permissions = claims_for(session, user); refresh = new_refresh_token(); now = datetime.now(timezone.utc)
    session.add(RefreshToken(user_id=user.id, token_hash=token_hash(refresh), expires_at=now + timedelta(days=int(getenv("PLATFORM_REFRESH_TOKEN_TTL_DAYS", "30")))))
    scopes = user_scopes(session, user.id)
    return {"access_token": issue_access_token(user, roles, permissions, scopes, effective_access(scopes)), "refresh_token": refresh, "token_type": "bearer", "expires_in": int(getenv("PLATFORM_ACCESS_TOKEN_TTL_SECONDS", "900"))}
def require_permission(request, permission):
    claims = bearer_claims(request)
    if "*" not in claims["permissions"] and permission not in claims["permissions"]: raise HTTPException(403, "permission denied")
    return claims

def department_context(claims, scope="TENANT", supplied=None):
    scope = scope.upper()
    claimed = claims.get("tenant_id")
    if claimed and "*" not in claims["permissions"]:
        if scope != "TENANT": raise HTTPException(403, "platform department access denied")
        if supplied and str(supplied) != str(claimed): raise HTTPException(403, "tenant access denied")
        return scope, UUID(str(claimed))
    if scope == "PLATFORM": return scope, None
    if supplied is None: raise HTTPException(422, "tenant_id is required for tenant departments")
    return scope, UUID(str(supplied))

def safe_department(item):
    return {"id": str(item.id), "scope": item.scope, "tenant_id": str(item.tenant_id) if item.tenant_id else None, "name": item.name, "code": item.code,
            "description": item.description, "enabled": item.enabled, "created_at": item.created_at, "updated_at": item.updated_at}

def role_permissions(session, role_id):
    return sorted(session.scalars(select(Permission.name).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == role_id)))

def safe_role(session, item):
    return {"id": str(item.id), "scope": item.scope, "tenant_id": str(item.tenant_id) if item.tenant_id else None,
            "department_id": str(item.department_id) if item.department_id else None, "name": item.display_name or item.name,
            "code": item.code, "description": item.description, "enabled": item.enabled,
            "permissions": role_permissions(session, item.id), "created_at": item.created_at, "updated_at": item.updated_at}

def set_role_permissions(session, role, names):
    unknown = set(names) - PERMISSIONS
    if unknown: raise HTTPException(422, f"unknown permissions: {', '.join(sorted(unknown))}")
    existing = {binding.permission_id: binding for binding in session.scalars(select(RolePermission).where(RolePermission.role_id == role.id))}
    desired = {session.scalar(select(Permission.id).where(Permission.name == name)) for name in set(names)}
    for permission_id, binding in existing.items():
        if permission_id not in desired: session.delete(binding)
    for permission_id in desired:
        if permission_id not in existing: session.add(RolePermission(role_id=role.id, permission_id=permission_id))

def access_template_permissions(session, template_id):
    return sorted(session.scalars(
        select(Permission.name)
        .join(AccessTemplatePermission, AccessTemplatePermission.permission_id == Permission.id)
        .where(AccessTemplatePermission.template_id == template_id)
    ))

def safe_access_template(session, item):
    return {"id": str(item.id), "scope": item.scope,
            "tenant_id": str(item.tenant_id) if item.tenant_id else None,
            "target_type": item.target_type, "name": item.name, "code": item.code,
            "description": item.description, "enabled": item.enabled,
            "permissions": access_template_permissions(session, item.id),
            "created_at": item.created_at, "updated_at": item.updated_at}

def access_template_context(claims, scope, supplied_tenant_id=None):
    scope = scope.upper()
    if scope == "PLATFORM":
        if "*" not in claims["permissions"]: raise HTTPException(403, "platform access template permission denied")
        return None
    if scope != "TENANT": raise HTTPException(422, "invalid access template scope")
    if claims.get("franchise_id") and "*" not in claims["permissions"]:
        raise HTTPException(403, "franchise administrators cannot manage access templates")
    claimed = claims.get("tenant_id")
    if "*" not in claims["permissions"]:
        if not claimed or str(supplied_tenant_id or claimed) != str(claimed): raise HTTPException(403, "tenant access denied")
        return UUID(str(claimed))
    if not supplied_tenant_id: raise HTTPException(422, "tenant_id is required")
    return UUID(str(supplied_tenant_id))

def set_access_template_permissions(session, template, names, claims):
    unknown = set(names) - PERMISSIONS
    if unknown: raise HTTPException(422, f"unknown permissions: {', '.join(sorted(unknown))}")
    if "*" not in claims["permissions"] and not set(names).issubset(set(claims["permissions"])):
        raise HTTPException(403, "an access template cannot delegate permissions you do not have")
    existing = {item.permission_id: item for item in session.scalars(select(AccessTemplatePermission).where(AccessTemplatePermission.template_id == template.id))}
    desired = {session.scalar(select(Permission.id).where(Permission.name == name)) for name in set(names)}
    for permission_id, binding in existing.items():
        if permission_id not in desired: session.delete(binding)
    for permission_id in desired:
        if permission_id not in existing: session.add(AccessTemplatePermission(template_id=template.id, permission_id=permission_id))

def validate_access_template_menu(names, enabled):
    if enabled and not any(name in MENU_PERMISSIONS for name in names):
        raise HTTPException(422, "an active access template must allow at least one menu")
@asynccontextmanager
async def lifespan(_):
    # Schema changes are exclusively Alembic-owned; bootstrapping only uses an applied schema.
    with SessionLocal() as session:
        ensure_foundations(session)
        username, password = getenv("PLATFORM_BOOTSTRAP_ADMIN_USERNAME", "").strip(), getenv("PLATFORM_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if username and password and not session.scalar(select(PlatformUser).where(PlatformUser.username_normalized == username.lower())):
            user = PlatformUser(username=username, username_normalized=username.lower(), password_hash=hash_password(password)); session.add(user); session.flush(); assign_roles(session, user, ["PLATFORM_SUPER_ADMIN"]); session.add(UserAccessScope(user_id=user.id, scope_type="PLATFORM")); audit(session, "bootstrap_admin.created", user.id)
        session.commit()
    yield
app = FastAPI(title="Platform Core", version="1.0.0", docs_url="/internal/docs", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/health")
def health(): return {"status":"ok", "service":"platform-core-service"}
@app.post("/api/v1/auth/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(db)):
    remote = request.client.host if request.client else "unknown"
    if not limited(f"platform:login:{remote}", int(getenv("PLATFORM_LOGIN_RATE_LIMIT", "20"))):
        raise HTTPException(429, "login rate limit exceeded")
    principal = (payload.username or payload.email or "").lower()
    user = session.scalar(select(PlatformUser).where(or_(
        PlatformUser.username_normalized == principal,
        func.lower(PlatformUser.email) == principal,
    )))
    now = datetime.now(timezone.utc)
    if not user or not user.enabled or (user.locked_until and utc(user.locked_until) > now) or not verify_password(payload.password, user.password_hash):
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= int(getenv("PLATFORM_LOGIN_MAX_FAILURES", "5")): user.locked_until = now + timedelta(minutes=int(getenv("PLATFORM_LOGIN_LOCK_MINUTES", "15")))
            audit(session, "login.failed", user.id); session.commit()
        raise HTTPException(401, "invalid credentials")
    user.failed_login_count = 0; user.locked_until = None; user.last_login_at = now; result = tokens(session, user); audit(session, "login.succeeded", user.id); session.commit(); return result
@app.post("/api/v1/auth/refresh")
def refresh(payload: RefreshIn, session: Session = Depends(db)):
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(payload.refresh_token)))
    now = datetime.now(timezone.utc)
    if not row or row.revoked_at or utc(row.expires_at) <= now: raise HTTPException(401, "invalid refresh token")
    user = session.get(PlatformUser, row.user_id)
    if not user or not user.enabled: raise HTTPException(401, "invalid refresh token")
    row.revoked_at = now; result = tokens(session, user); session.flush()
    replacement = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(result["refresh_token"])))
    if replacement is None: raise RuntimeError("refresh token persistence failed")
    row.replaced_by_id = replacement.id; audit(session, "token.refreshed", user.id); session.commit(); return result
@app.post("/api/v1/auth/logout")
def logout(payload: RefreshIn, session: Session = Depends(db)):
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(payload.refresh_token)))
    if row and not row.revoked_at: row.revoked_at = datetime.now(timezone.utc); audit(session, "logout", row.user_id); session.commit()
    return {"revoked": True}
@app.get("/api/v1/auth/me")
def me(request: Request, session: Session = Depends(db)):
    claims = bearer_claims(request); user = session.get(PlatformUser, UUID(claims["sub"]))
    if not user or not user.enabled: raise HTTPException(401, "account disabled")
    return {"id": str(user.id), "username": user.username, "email": user.email,
            "full_name": user.full_name, "mobile": user.mobile,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "franchise_id": str(user.franchise_id) if user.franchise_id else None,
            "is_organization_owner": user.is_organization_owner,
            "membership_level": "FRANCHISE" if user.franchise_id else "TENANT" if user.tenant_id else "PLATFORM",
            "roles": claims["roles"], "permissions": claims["permissions"], "access_scopes": user_scopes(session, user.id),
            "effective_access": effective_access(user_scopes(session, user.id))}
@app.post("/api/v1/auth/change-password")
def change_password(payload: PasswordChangeIn, request: Request, session: Session = Depends(db)):
    claims = bearer_claims(request); user = session.get(PlatformUser, UUID(claims["sub"]))
    if not user or not verify_password(payload.current_password, user.password_hash): raise HTTPException(401, "invalid credentials")
    user.password_hash = hash_password(payload.new_password); user.password_changed_at = datetime.now(timezone.utc)
    for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))): token.revoked_at = datetime.now(timezone.utc)
    audit(session, "password.changed", user.id); session.commit(); return {"changed": True}
@app.get("/api/v1/platform/users")
def list_users(request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.read")
    statement = select(PlatformUser).order_by(PlatformUser.full_name, PlatformUser.username)
    if "*" in claims["permissions"]:
        # The platform team directory contains platform employees only. Tenant
        # and franchise administrators remain visible from their organization.
        statement = statement.where(PlatformUser.tenant_id.is_(None), PlatformUser.franchise_id.is_(None))
    elif claims.get("franchise_id"):
        statement = statement.where(PlatformUser.franchise_id == UUID(claims["franchise_id"]))
    elif claims.get("tenant_id"):
        statement = statement.where(PlatformUser.tenant_id == UUID(claims["tenant_id"]), PlatformUser.franchise_id.is_(None))
    return [safe_user(session, user) for user in session.scalars(statement)]

@app.get("/api/v1/platform/users/{user_id}")
def get_user(user_id: UUID, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.read"); user = session.get(PlatformUser, user_id)
    if not user or (claims.get("tenant_id") and "*" not in claims["permissions"] and str(user.tenant_id) != claims["tenant_id"]): raise HTTPException(404, "user not found")
    if claims.get("franchise_id") and "*" not in claims["permissions"] and str(user.franchise_id) != claims["franchise_id"]: raise HTTPException(404, "user not found")
    return safe_user(session, user)

@app.get("/api/v1/platform/departments")
def list_departments(request: Request, scope: str = "TENANT", tenant_id: UUID | None = None, q: str | None = None, include_inactive: bool = True, session: Session = Depends(db)):
    claims = require_permission(request, "platform.departments.read")
    selected_scope, selected_tenant = department_context(claims, scope, tenant_id)
    statement = select(Department).where(Department.scope == selected_scope).order_by(Department.name)
    statement = statement.where(Department.tenant_id == selected_tenant) if selected_tenant else statement.where(Department.tenant_id.is_(None))
    if not include_inactive: statement = statement.where(Department.enabled.is_(True))
    if q:
        search = f"%{q.strip()}%"
        statement = statement.where(or_(Department.name.ilike(search), Department.code.ilike(search)))
    return [safe_department(item) for item in session.scalars(statement)]

@app.post("/api/v1/platform/departments", status_code=201)
def create_department(payload: DepartmentCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.departments.manage")
    scope, tenant_id = department_context(claims, payload.scope, payload.tenant_id)
    code = payload.code.strip().upper()
    duplicate_query = select(Department.id).where(Department.scope == scope, Department.code == code)
    duplicate_query = duplicate_query.where(Department.tenant_id == tenant_id) if tenant_id else duplicate_query.where(Department.tenant_id.is_(None))
    if session.scalar(duplicate_query): raise HTTPException(409, "department code already exists in this scope")
    item = Department(scope=scope, tenant_id=tenant_id, name=payload.name, code=code, description=payload.description, enabled=payload.enabled)
    session.add(item); session.flush(); audit(session, "department.created", UUID(claims["sub"])); session.commit(); session.refresh(item)
    return safe_department(item)

@app.patch("/api/v1/platform/departments/{department_id}")
def update_department(department_id: UUID, payload: DepartmentUpdateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.departments.manage")
    item = session.get(Department, department_id)
    if not item: raise HTTPException(404, "department not found")
    department_context(claims, item.scope, item.tenant_id)
    values = payload.model_dump(exclude_unset=True)
    if "code" in values:
        values["code"] = values["code"].strip().upper()
        duplicate_query = select(Department.id).where(Department.scope == item.scope, Department.code == values["code"], Department.id != item.id)
        duplicate_query = duplicate_query.where(Department.tenant_id == item.tenant_id) if item.tenant_id else duplicate_query.where(Department.tenant_id.is_(None))
        if session.scalar(duplicate_query): raise HTTPException(409, "department code already exists in this scope")
    for key, value in values.items(): setattr(item, key, value)
    audit(session, "department.updated", UUID(claims["sub"])); session.commit(); session.refresh(item)
    return safe_department(item)

@app.delete("/api/v1/platform/departments/{department_id}", status_code=204)
def delete_department(department_id: UUID, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.departments.manage")
    item = session.get(Department, department_id)
    if not item: raise HTTPException(404, "department not found")
    department_context(claims, item.scope, item.tenant_id)
    if session.scalar(select(Role.id).where(Role.department_id == item.id)): raise HTTPException(409, "department has roles; move or delete those roles first")
    session.delete(item); audit(session, "department.deleted", UUID(claims["sub"])); session.commit()

@app.get("/api/v1/platform/permissions")
def list_permissions(request: Request):
    claims = bearer_claims(request)
    if "*" not in claims["permissions"] and not ({"platform.roles.manage", "platform.access_templates.manage"} & set(claims["permissions"])):
        raise HTTPException(403, "permission catalog access denied")
    delegable = PERMISSIONS if "*" in claims["permissions"] else (PERMISSIONS & set(claims["permissions"]))
    result = []
    for name in sorted(delegable):
        if name in MENU_PERMISSIONS:
            group, label = MENU_PERMISSIONS[name]
            result.append({"name": name, "type": "MENU", "group": group, "section": label, "label": label})
        else:
            parts = name.split(".")
            namespaced = {"aaa", "ai", "bss", "crm", "device", "nms", "oss", "platform", "support"}
            resource = parts[1] if parts[0] in namespaced and len(parts) > 1 else parts[0]
            section_aliases = {
                "lead": "Leads", "leads": "Leads", "customer": "Customers", "customers": "Customers",
                "kyc": "KYC documents", "document": "KYC documents", "franchise": "Franchises",
                "connections": "Connections", "followup": "Follow-ups", "audit": "Audit",
                "users": "Administrators", "roles": "Roles", "departments": "Departments",
                "nas": "NAS", "subscribers": "Subscribers", "sessions": "Sessions", "radius": "RADIUS",
                "radius_server": "RADIUS servers", "ip": "IP identity", "ip_pool": "IP pools",
                "secret": "Secrets", "policy": "Policies", "subscriber_policy": "Subscriber policies",
                "session": "Sessions", "accounting": "Accounting", "control": "Control actions",
                "fup": "FUP", "router": "RouterOS", "usage": "Usage", "plan": "Service plans",
                "ticket": "Tickets", "diagnostic": "Diagnostics", "outage": "Outages",
                "kb": "Knowledge base", "sla": "SLA", "workorders": "Work orders",
                "technicians": "Technicians", "inventory": "Inventory", "device": "Devices",
                "firmware": "Firmware", "alerts": "Alerts", "incidents": "Incidents",
                "slo": "Service objectives", "root_cause": "Root cause", "postmortem": "Postmortems",
                "models": "AI models", "datasets": "Datasets", "remediation": "Remediation",
                "events": "Security events", "cases": "Security cases", "violations": "Violations",
                "vuln": "Vulnerabilities", "consent": "Consent", "dsar": "Data requests",
                "analytics": "Analytics", "partners": "Partners", "agreements": "Agreements",
                "settlements": "Settlements", "commissions": "Commissions", "payouts": "Payouts",
                "tenants": "Tenants",
            }
            section = section_aliases.get(resource, resource.replace("_", " ").title())
            group_aliases = {
                "alerts": "ASSURANCE", "changes": "ASSURANCE", "dashboards": "ASSURANCE",
                "incidents": "ASSURANCE", "maintenance": "ASSURANCE", "postmortem": "ASSURANCE",
                "root_cause": "ASSURANCE", "slo": "ASSURANCE", "synthetic": "ASSURANCE",
                "technicians": "WORKFORCE", "workorders": "WORKFORCE", "dispatch": "WORKFORCE",
                "visits": "WORKFORCE", "shifts": "WORKFORCE", "inventory": "WORKFORCE",
                "proof": "WORKFORCE", "fieldops": "WORKFORCE", "escalations": "WORKFORCE",
                "feedback": "WORKFORCE", "location": "WORKFORCE",
                "predictions": "INTELLIGENCE", "recommendations": "INTELLIGENCE", "fraud": "INTELLIGENCE",
                "churn": "INTELLIGENCE", "retention": "INTELLIGENCE", "features": "INTELLIGENCE",
                "datasets": "INTELLIGENCE", "models": "INTELLIGENCE", "training": "INTELLIGENCE",
                "deploy": "INTELLIGENCE", "monitoring": "INTELLIGENCE", "remediation": "INTELLIGENCE",
                "kill_switch": "INTELLIGENCE", "events": "SECURITY", "cases": "SECURITY",
                "violations": "SECURITY", "policies": "SECURITY", "vuln": "SECURITY",
                "consent": "SECURITY", "dsar": "SECURITY", "evidence": "SECURITY", "li": "SECURITY",
                "analytics": "WAREHOUSE", "domains": "TENANCY", "config": "TENANCY",
                "entitlements": "TENANCY", "quota": "TENANCY", "org": "TENANCY",
                "partners": "TENANCY", "agreements": "TENANCY", "ownership": "TENANCY",
                "grants": "TENANCY", "memberships": "TENANCY", "commissions": "TENANCY",
                "settlements": "TENANCY", "payouts": "TENANCY", "wallet": "TENANCY",
                "governance": "TENANCY",
            }
            action_parts = parts[2:] if parts[0] in namespaced else parts[1:]
            action_label = " ".join(action_parts or ["view"]).replace("_", " ").title()
            result.append({"name": name, "type": "ACTION", "group": group_aliases.get(parts[0], parts[0].upper()), "section": section, "label": action_label})
    return result

@app.get("/api/v1/platform/roles")
def list_roles(request: Request, scope: str = "TENANT", tenant_id: UUID | None = None, department_id: UUID | None = None, session: Session = Depends(db)):
    claims = bearer_claims(request)
    if "*" not in claims["permissions"] and not ({"platform.roles.read", "platform.roles.manage"} & set(claims["permissions"])): raise HTTPException(403, "permission denied")
    selected_scope, selected_tenant = department_context(claims, scope, tenant_id)
    statement = select(Role).where(Role.scope == selected_scope).order_by(Role.display_name)
    statement = statement.where(Role.tenant_id == selected_tenant) if selected_tenant else statement.where(Role.tenant_id.is_(None))
    if department_id: statement = statement.where(Role.department_id == department_id)
    return [safe_role(session, item) for item in session.scalars(statement)]

@app.get("/api/v1/platform/access-templates")
def list_access_templates(request: Request, scope: str = "PLATFORM", tenant_id: UUID | None = None,
                          target_type: str | None = None, include_inactive: bool = False,
                          session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.read")
    selected_tenant = access_template_context(claims, scope, tenant_id)
    statement = select(AccessTemplate).where(AccessTemplate.scope == scope.upper())
    statement = statement.where(AccessTemplate.tenant_id == selected_tenant) if selected_tenant else statement.where(AccessTemplate.tenant_id.is_(None))
    if target_type: statement = statement.where(AccessTemplate.target_type == target_type.upper())
    if not include_inactive: statement = statement.where(AccessTemplate.enabled.is_(True))
    return [safe_access_template(session, item) for item in session.scalars(statement.order_by(AccessTemplate.name))]

@app.post("/api/v1/platform/access-templates", status_code=201)
def create_access_template(payload: AccessTemplateCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.manage")
    selected_tenant = access_template_context(claims, payload.scope, payload.tenant_id)
    if payload.scope == "PLATFORM" and payload.target_type != "TENANT": raise HTTPException(422, "platform templates must target tenants")
    if payload.scope == "TENANT" and payload.target_type != "FRANCHISE": raise HTTPException(422, "tenant templates must target franchises")
    code = payload.code.upper()
    if session.scalar(select(AccessTemplate.id).where(AccessTemplate.scope == payload.scope,
            AccessTemplate.tenant_id == selected_tenant, AccessTemplate.code == code)):
        raise HTTPException(409, "access template code already exists")
    validate_access_template_menu(payload.permissions, payload.enabled)
    item = AccessTemplate(scope=payload.scope, tenant_id=selected_tenant, target_type=payload.target_type,
        name=payload.name, code=code, description=payload.description, enabled=payload.enabled)
    session.add(item); session.flush(); set_access_template_permissions(session, item, payload.permissions, claims)
    audit(session, "access_template.created", UUID(claims["sub"])); session.commit(); session.refresh(item)
    return safe_access_template(session, item)

@app.patch("/api/v1/platform/access-templates/{template_id}")
def update_access_template(template_id: UUID, payload: AccessTemplateUpdateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.manage")
    item = session.get(AccessTemplate, template_id)
    if not item: raise HTTPException(404, "access template not found")
    access_template_context(claims, item.scope, item.tenant_id)
    values = payload.model_dump(exclude_unset=True); names = values.pop("permissions", None)
    if "code" in values: values["code"] = values["code"].upper()
    effective_names = names if names is not None else access_template_permissions(session, item.id)
    validate_access_template_menu(effective_names, values.get("enabled", item.enabled))
    if names is not None: set_access_template_permissions(session, item, names, claims)
    for key, value in values.items(): setattr(item, key, value)
    audit(session, "access_template.updated", UUID(claims["sub"])); session.commit(); session.refresh(item)
    return safe_access_template(session, item)

@app.delete("/api/v1/platform/access-templates/{template_id}", status_code=204)
def delete_access_template(template_id: UUID, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.manage")
    item = session.get(AccessTemplate, template_id)
    if not item: raise HTTPException(404, "access template not found")
    access_template_context(claims, item.scope, item.tenant_id)
    if session.scalar(select(OrganizationAccessTemplate.id).where(OrganizationAccessTemplate.template_id == item.id)):
        raise HTTPException(409, "access template is assigned; deactivate it instead")
    session.delete(item); audit(session, "access_template.deleted", UUID(claims["sub"])); session.commit()

@app.get("/api/v1/platform/access-template-assignments/{target_type}/{target_id}")
def get_access_template_assignment(target_type: str, target_id: UUID, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.read")
    target_type = target_type.upper()
    binding = session.scalar(select(OrganizationAccessTemplate).where(
        OrganizationAccessTemplate.target_type == target_type, OrganizationAccessTemplate.target_id == target_id))
    if not binding: return None
    item = session.get(AccessTemplate, binding.template_id)
    access_template_context(claims, item.scope, item.tenant_id)
    return {"target_type": target_type, "target_id": str(target_id), "template": safe_access_template(session, item)}

@app.put("/api/v1/platform/access-template-assignments/{target_type}/{target_id}")
def assign_access_template(target_type: str, target_id: UUID, payload: AccessTemplateAssignmentIn,
                           request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.access_templates.manage")
    target_type = target_type.upper(); item = session.get(AccessTemplate, payload.template_id)
    if not item or not item.enabled or item.target_type != target_type: raise HTTPException(422, "invalid access template for this organization")
    access_template_context(claims, item.scope, item.tenant_id)
    if target_type == "TENANT":
        if item.scope != "PLATFORM" or "*" not in claims["permissions"]: raise HTTPException(403, "tenant templates are assigned by platform administrators")
    elif target_type == "FRANCHISE":
        if item.scope != "TENANT": raise HTTPException(422, "franchises require a tenant access template")
        franchise = effective_access([{"scope_type": "FRANCHISE", "scope_id": str(target_id)}])
        if str(item.tenant_id) not in franchise["tenant_ids"]: raise HTTPException(422, "template and franchise belong to different tenants")
    else: raise HTTPException(422, "unsupported access template target")
    binding = session.scalar(select(OrganizationAccessTemplate).where(
        OrganizationAccessTemplate.target_type == target_type, OrganizationAccessTemplate.target_id == target_id))
    if binding: binding.template_id = item.id; binding.assigned_by = UUID(claims["sub"])
    else: session.add(OrganizationAccessTemplate(target_type=target_type, target_id=target_id,
        template_id=item.id, assigned_by=UUID(claims["sub"])))
    audit(session, "access_template.assigned", UUID(claims["sub"])); session.commit()
    return {"target_type": target_type, "target_id": str(target_id), "template": safe_access_template(session, item)}

@app.post("/api/v1/platform/roles", status_code=201)
def create_role(payload: RoleCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.roles.manage")
    scope, tenant_id = department_context(claims, payload.scope, payload.tenant_id)
    department = session.get(Department, payload.department_id)
    if not department or department.scope != scope or department.tenant_id != tenant_id: raise HTTPException(422, "department does not belong to this role scope")
    code = payload.code.strip().upper()
    if session.scalar(select(Role.id).where(Role.scope == scope, Role.tenant_id == tenant_id, Role.code == code)): raise HTTPException(409, "role code already exists in this scope")
    internal_name = f"{scope}:{tenant_id or 'PLATFORM'}:{code}"
    item = Role(name=internal_name, display_name=payload.name, code=code, description=payload.description, scope=scope,
                tenant_id=tenant_id, department_id=department.id, enabled=payload.enabled, global_role=scope == "PLATFORM")
    session.add(item); session.flush(); set_role_permissions(session, item, payload.permissions)
    audit(session, "role.created", UUID(claims["sub"])); session.commit(); session.refresh(item); return safe_role(session, item)

@app.patch("/api/v1/platform/roles/{role_id}")
def update_role(role_id: UUID, payload: RoleUpdateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.roles.manage"); item = session.get(Role, role_id)
    if not item or item.scope == "SYSTEM": raise HTTPException(404, "role not found")
    department_context(claims, item.scope, item.tenant_id)
    values = payload.model_dump(exclude_unset=True); permissions = values.pop("permissions", None)
    if "department_id" in values:
        department = session.get(Department, values["department_id"])
        if not department or department.scope != item.scope or department.tenant_id != item.tenant_id: raise HTTPException(422, "department does not belong to this role scope")
    if "name" in values: values["display_name"] = values.pop("name")
    if "code" in values:
        values["code"] = values["code"].strip().upper()
        if session.scalar(select(Role.id).where(Role.scope == item.scope, Role.tenant_id == item.tenant_id, Role.code == values["code"], Role.id != item.id)): raise HTTPException(409, "role code already exists in this scope")
        values["name"] = f"{item.scope}:{item.tenant_id or 'PLATFORM'}:{values['code']}"
    for key, value in values.items(): setattr(item, key, value)
    if permissions is not None: set_role_permissions(session, item, permissions)
    audit(session, "role.updated", UUID(claims["sub"])); session.commit(); session.refresh(item); return safe_role(session, item)

@app.delete("/api/v1/platform/roles/{role_id}", status_code=204)
def delete_role(role_id: UUID, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.roles.manage"); item = session.get(Role, role_id)
    if not item or item.scope == "SYSTEM": raise HTTPException(404, "role not found")
    department_context(claims, item.scope, item.tenant_id)
    if session.scalar(select(UserRole.id).where(UserRole.role_id == item.id)): raise HTTPException(409, "role is assigned to one or more users; deactivate it instead")
    session.delete(item); audit(session, "role.deleted", UUID(claims["sub"])); session.commit()
@app.post("/api/v1/platform/users", status_code=201)
def create_user(payload: UserCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.create")
    tenant_id = UUID(claims["tenant_id"]) if claims.get("tenant_id") and "*" not in claims["permissions"] else payload.tenant_id
    franchise_id = UUID(claims["franchise_id"]) if claims.get("franchise_id") and "*" not in claims["permissions"] else payload.franchise_id
    scopes = payload.access_scopes
    if not scopes:
        from .schemas import AccessScopeIn
        scopes = [AccessScopeIn(scope_type="FRANCHISE", scope_id=franchise_id)] if franchise_id else [AccessScopeIn(scope_type="TENANT", scope_id=tenant_id)] if tenant_id else [AccessScopeIn(scope_type="PLATFORM")]
    validate_delegated_scopes(claims, scopes)
    if payload.is_organization_owner and "*" not in claims["permissions"]:
        raise HTTPException(403, "only a platform administrator can create an organization owner")
    if payload.is_organization_owner and not (tenant_id or franchise_id):
        raise HTTPException(422, "an organization owner must belong to a tenant or franchise")
    username_normalized, email_normalized, mobile_normalized = ensure_unique_user_identity(session, payload.username, payload.email, payload.mobile)
    user = PlatformUser(username=payload.username.strip(), username_normalized=username_normalized,
        email=email_normalized, email_normalized=email_normalized, full_name=payload.full_name,
        mobile=payload.mobile, mobile_normalized=mobile_normalized, tenant_id=tenant_id,
        franchise_id=franchise_id, department_id=payload.department_id,
        is_organization_owner=payload.is_organization_owner, password_hash=hash_password(payload.password))
    session.add(user)
    try:
        session.flush()
        if payload.role_ids: set_user_roles(session, user, payload.role_ids, claims, payload.department_id)
        else: assign_roles(session, user, payload.roles)
        set_user_scopes(session, user, scopes, UUID(claims["sub"])); audit(session, "user.created", user.id); session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "username, email address, or mobile number already exists") from exc
    return safe_user(session, user)
@app.patch("/api/v1/platform/users/{user_id}")
def update_user(user_id: UUID, payload: UserUpdateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.update"); user = session.get(PlatformUser, user_id)
    if not user or (claims.get("tenant_id") and "*" not in claims["permissions"] and str(user.tenant_id) != claims["tenant_id"]): raise HTTPException(404, "user not found")
    if claims.get("franchise_id") and "*" not in claims["permissions"] and str(user.franchise_id) != claims["franchise_id"]: raise HTTPException(404, "user not found")
    values = payload.model_dump(exclude_unset=True); role_ids = values.pop("role_ids", None)
    if values.get("enabled") is False and user.is_organization_owner:
        owner_scope = (PlatformUser.franchise_id == user.franchise_id) if user.franchise_id else (PlatformUser.tenant_id == user.tenant_id)
        statement = select(PlatformUser.id).where(owner_scope, PlatformUser.is_organization_owner.is_(True), PlatformUser.enabled.is_(True), PlatformUser.id != user.id)
        if not user.franchise_id: statement = statement.where(PlatformUser.franchise_id.is_(None))
        other_owner = session.scalar(statement)
        if not other_owner: raise HTTPException(409, "the last active organization owner cannot be disabled")
    scopes = payload.access_scopes if "access_scopes" in payload.model_fields_set else None
    values.pop("access_scopes", None)
    if "email" in values:
        normalized = normalize_email(values["email"])
        if normalized and session.scalar(select(PlatformUser.id).where(PlatformUser.email_normalized == normalized, PlatformUser.id != user.id)): raise HTTPException(409, "email address already exists")
        values["email"] = normalized; values["email_normalized"] = normalized
    if "mobile" in values:
        normalized = normalize_mobile(values["mobile"])
        if normalized and session.scalar(select(PlatformUser.id).where(PlatformUser.mobile_normalized == normalized, PlatformUser.id != user.id)): raise HTTPException(409, "mobile number already exists")
        values["mobile_normalized"] = normalized
    if scopes is not None: validate_delegated_scopes(claims, scopes); set_user_scopes(session, user, scopes, UUID(claims["sub"]))
    selected_department = values.get("department_id", user.department_id)
    if role_ids is not None: set_user_roles(session, user, role_ids, claims, selected_department)
    for key, value in values.items(): setattr(user, key, value)
    audit(session, "user.updated", user.id); session.commit(); session.refresh(user); return safe_user(session, user)
@app.post("/api/v1/platform/users/{user_id}/reset-password")
def reset_password(user_id: UUID, payload: AdminPasswordResetIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.update"); user = session.get(PlatformUser, user_id)
    if not user or (claims.get("tenant_id") and "*" not in claims["permissions"] and str(user.tenant_id) != claims["tenant_id"]): raise HTTPException(404, "user not found")
    user.password_hash = hash_password(payload.new_password); user.password_changed_at = datetime.now(timezone.utc)
    for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))): token.revoked_at = datetime.now(timezone.utc)
    audit(session, "password.reset", user.id); session.commit(); return {"reset": True}
@app.post("/api/v1/platform/service-accounts", status_code=201)
def create_service_account(payload: ServiceAccountCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.roles.manage")
    if claims.get("tenant_id") and "*" not in claims["permissions"] and str(payload.tenant_id) != claims["tenant_id"]: raise HTTPException(403, "tenant access denied")
    if not set(payload.permissions).issubset(PERMISSIONS): raise HTTPException(422, "unknown permission")
    if session.scalar(select(ServiceAccount).where(ServiceAccount.name == payload.name)): raise HTTPException(409, "service account already exists")
    key = new_refresh_token(); account = ServiceAccount(name=payload.name, tenant_id=payload.tenant_id, key_hash=token_hash(key), permissions=__import__("json").dumps(sorted(payload.permissions))); session.add(account); audit(session, "service_account.created"); session.commit()
    return {"id": str(account.id), "name": account.name, "api_key": key}
@app.post("/internal/auth/service-token")
def service_token(request: Request, session: Session = Depends(db)):
    key = request.headers.get("X-Platform-Service-Key", "")
    account = session.scalar(select(ServiceAccount).where(ServiceAccount.key_hash == token_hash(key))) if key else None
    if not account or not account.enabled: raise HTTPException(401, "service account authentication failed")
    account.last_used_at = datetime.now(timezone.utc); audit(session, "service_account.token_issued"); session.commit()
    return {"access_token": issue_service_access_token(account.id, account.tenant_id, __import__("json").loads(account.permissions)), "token_type": "bearer", "expires_in": int(getenv("PLATFORM_SERVICE_TOKEN_TTL_SECONDS", "300"))}
