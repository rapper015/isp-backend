"""Database engine/session for the Tenancy Service (control plane).

This service owns the control-plane database (shared schema, `ten_` tables).
The `DatabaseRouter`/`TenantContext` layer guarantees that tenant-owned rows
are only reached through a validated TenantContext and FAILS CLOSED otherwise."""
from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


url = getenv("DATABASE_URL", "sqlite:///./tenancy.db")
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
