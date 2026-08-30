"""Identity Service API — application users + login/token issuance.

Separate from the AAA service (FreeRADIUS/NAS subscriber auth). Issues JWTs
that every service's management API verifies (shared <SVC>_JWT_SECRET)."""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from os import getenv

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import User
from .schemas import LoginIn, UserCreateIn
from .security import (bearer_claims, hash_password, internal_service_auth,
                       issue_access_token, platform_jwt_secret, verify_password)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Bootstrap the first platform admin from env if no users exist yet.
    username = getenv("IDENTITY_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = getenv("IDENTITY_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if username and password:
        with SessionLocal() as bootstrap:
            existing = bootstrap.scalar(select(User).where(
                User.username_normalized == username.lower()))
            if existing is None:
                bootstrap.add(User(
                    username=username, username_normalized=username.lower(),
                    role="PLATFORM_ADMIN", password_hash=hash_password(password),
                    status="ACTIVE"))
                bootstrap.commit()
    yield


app = FastAPI(title="Identity Service", version="1.0.0", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "identity-service")}


@app.get("/status")
def status():
    return {"service": "identity", "phase": "auth"}


def _do_login(session: Session, username: str, password: str) -> dict:
    normalized = username.strip().lower()
    user = session.scalar(select(User).where(User.username_normalized == normalized))
    if not user or user.status != "ACTIVE" or not verify_password(password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    token = issue_access_token(user.id, user.username, user.role, user.tenant_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(getenv("IDENTITY_TOKEN_TTL_SECONDS", "43200")),
        "user": {"id": str(user.id), "username": user.username,
                 "full_name": user.full_name, "email": user.email, "role": user.role},
    }


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(db)):
    return _do_login(session, payload.username, payload.password)


@app.post("/admin-login")
def admin_login(payload: LoginIn, request: Request, session: Session = Depends(db)):
    return _do_login(session, payload.username, payload.password)


@app.post("/api/auth/users", status_code=201, dependencies=[Depends(internal_service_auth)])
def create_user(payload: UserCreateIn, session: Session = Depends(db)):
    normalized = payload.username.strip().lower()
    existing = session.scalar(select(User).where(User.username_normalized == normalized))
    if existing:
        raise HTTPException(409, "username already exists")
    user = User(tenant_id=payload.tenant_id, username=payload.username.strip(),
                username_normalized=normalized, full_name=payload.full_name,
                email=payload.email, role=payload.role or "READ_ONLY",
                password_hash=hash_password(payload.password), status="ACTIVE")
    session.add(user)
    session.commit()
    return {"id": str(user.id), "username": user.username, "full_name": user.full_name,
            "email": user.email, "role": user.role, "status": user.status}


@app.get("/api/auth/users", dependencies=[Depends(internal_service_auth)])
def list_users(session: Session = Depends(db)):
    rows = session.scalars(select(User).order_by(User.username)).all()
    return [{"id": str(u.id), "username": u.username, "full_name": u.full_name,
             "email": u.email, "role": u.role, "status": u.status} for u in rows]


@app.get("/api/auth/me")
def auth_me(request: Request, session: Session = Depends(db)):
    claims = bearer_claims(request)
    try:
        user_id = uuid.UUID(str(claims.get("userId")))
    except (ValueError, TypeError):
        raise HTTPException(401, "invalid token payload")
    user = session.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(404, "user not found")
    return {"id": str(user.id), "username": user.username, "full_name": user.full_name,
            "email": user.email, "role": user.role, "status": user.status,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None}
