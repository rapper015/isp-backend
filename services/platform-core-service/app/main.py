"""Platform operator authentication. It never authenticates RADIUS subscribers."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from os import getenv
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Permission, PlatformUser, RefreshToken, Role, RolePermission, SecurityAuditEvent, ServiceAccount, UserRole
from .schemas import AdminPasswordResetIn, LoginIn, PasswordChangeIn, RefreshIn, ServiceAccountCreateIn, UserCreateIn
from .security import bearer_claims, hash_password, issue_access_token, issue_service_access_token, limited, new_refresh_token, token_hash, verify_password

PERMISSIONS = {"platform.users.read", "platform.users.create", "platform.users.update", "platform.users.disable", "platform.roles.manage", "aaa.nas.read", "aaa.nas.manage", "aaa.subscribers.read", "aaa.subscribers.manage", "aaa.sessions.read", "aaa.sessions.disconnect", "aaa.radius.manage", "crm.leads.read", "crm.leads.manage", "crm.customers.read", "crm.customers.manage", "crm.connections.manage"}
ROLE_PERMISSIONS = {"PLATFORM_SUPER_ADMIN": {"*"}, "TENANT_ADMIN": PERMISSIONS, "READ_ONLY": {"platform.users.read", "aaa.nas.read", "aaa.subscribers.read", "aaa.sessions.read", "crm.leads.read", "crm.customers.read"}}

def utc(value):
    """Normalize SQLite's naive timestamps and PostgreSQL's aware timestamps."""
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()
def audit(session, action, user_id=None): session.add(SecurityAuditEvent(user_id=user_id, action=action))
def ensure_foundations(session):
    for name in sorted(PERMISSIONS):
        if not session.scalar(select(Permission).where(Permission.name == name)): session.add(Permission(name=name))
    session.flush()
    for name, names in ROLE_PERMISSIONS.items():
        role = session.scalar(select(Role).where(Role.name == name))
        if not role: role = Role(name=name, global_role=name == "PLATFORM_SUPER_ADMIN"); session.add(role); session.flush()
        if "*" not in names:
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
def claims_for(session, user):
    role_rows = session.execute(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).scalars().all()
    if "PLATFORM_SUPER_ADMIN" in role_rows: return role_rows, {"*"}
    permissions = set(session.execute(select(Permission.name).join(RolePermission, RolePermission.permission_id == Permission.id).join(Role, Role.id == RolePermission.role_id).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).scalars())
    return role_rows, permissions
def tokens(session, user):
    roles, permissions = claims_for(session, user); refresh = new_refresh_token(); now = datetime.now(timezone.utc)
    session.add(RefreshToken(user_id=user.id, token_hash=token_hash(refresh), expires_at=now + timedelta(days=int(getenv("PLATFORM_REFRESH_TOKEN_TTL_DAYS", "30")))))
    return {"access_token": issue_access_token(user, roles, permissions), "refresh_token": refresh, "token_type": "bearer", "expires_in": int(getenv("PLATFORM_ACCESS_TOKEN_TTL_SECONDS", "900"))}
def require_permission(request, permission):
    claims = bearer_claims(request)
    if "*" not in claims["permissions"] and permission not in claims["permissions"]: raise HTTPException(403, "permission denied")
    return claims
@asynccontextmanager
async def lifespan(_):
    # Schema changes are exclusively Alembic-owned; bootstrapping only uses an applied schema.
    with SessionLocal() as session:
        ensure_foundations(session)
        username, password = getenv("PLATFORM_BOOTSTRAP_ADMIN_USERNAME", "").strip(), getenv("PLATFORM_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if username and password and not session.scalar(select(PlatformUser).where(PlatformUser.username_normalized == username.lower())):
            user = PlatformUser(username=username, username_normalized=username.lower(), password_hash=hash_password(password)); session.add(user); session.flush(); assign_roles(session, user, ["PLATFORM_SUPER_ADMIN"]); audit(session, "bootstrap_admin.created", user.id)
        session.commit()
    yield
app = FastAPI(title="Platform Core", version="1.0.0", docs_url="/internal/docs", lifespan=lifespan)
@app.get("/health")
def health(): return {"status":"ok", "service":"platform-core-service"}
@app.post("/api/v1/auth/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(db)):
    remote = request.client.host if request.client else "unknown"
    if not limited(f"platform:login:{remote}", int(getenv("PLATFORM_LOGIN_RATE_LIMIT", "20"))):
        raise HTTPException(429, "login rate limit exceeded")
    principal = payload.username.lower()
    user = session.scalar(select(PlatformUser).where(or_(PlatformUser.username_normalized == principal, PlatformUser.email == principal)))
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
    row.revoked_at = now; result = tokens(session, user); replacement = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(result["refresh_token"]))); row.replaced_by_id = replacement.id; audit(session, "token.refreshed", user.id); session.commit(); return result
@app.post("/api/v1/auth/logout")
def logout(payload: RefreshIn, session: Session = Depends(db)):
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(payload.refresh_token)))
    if row and not row.revoked_at: row.revoked_at = datetime.now(timezone.utc); audit(session, "logout", row.user_id); session.commit()
    return {"revoked": True}
@app.get("/api/v1/auth/me")
def me(request: Request, session: Session = Depends(db)):
    claims = bearer_claims(request); user = session.get(PlatformUser, UUID(claims["sub"]))
    if not user or not user.enabled: raise HTTPException(401, "account disabled")
    return {"id": str(user.id), "username": user.username, "email": user.email, "tenant_id": str(user.tenant_id) if user.tenant_id else None, "roles": claims["roles"], "permissions": claims["permissions"]}
@app.post("/api/v1/auth/change-password")
def change_password(payload: PasswordChangeIn, request: Request, session: Session = Depends(db)):
    claims = bearer_claims(request); user = session.get(PlatformUser, UUID(claims["sub"]))
    if not user or not verify_password(payload.current_password, user.password_hash): raise HTTPException(401, "invalid credentials")
    user.password_hash = hash_password(payload.new_password); user.password_changed_at = datetime.now(timezone.utc)
    for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))): token.revoked_at = datetime.now(timezone.utc)
    audit(session, "password.changed", user.id); session.commit(); return {"changed": True}
@app.post("/api/v1/platform/users", status_code=201)
def create_user(payload: UserCreateIn, request: Request, session: Session = Depends(db)):
    claims = require_permission(request, "platform.users.create")
    if payload.tenant_id and claims.get("tenant_id") and str(payload.tenant_id) != claims["tenant_id"]: raise HTTPException(403, "tenant access denied")
    if session.scalar(select(PlatformUser).where(PlatformUser.username_normalized == payload.username.lower())): raise HTTPException(409, "username already exists")
    user = PlatformUser(username=payload.username, username_normalized=payload.username.lower(), email=payload.email, full_name=payload.full_name, tenant_id=payload.tenant_id, password_hash=hash_password(payload.password)); session.add(user); session.flush(); assign_roles(session, user, payload.roles); audit(session, "user.created", user.id); session.commit(); return {"id":str(user.id), "username":user.username}
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
