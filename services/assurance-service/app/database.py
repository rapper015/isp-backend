"""Database engine/session for the Assurance Service.

Owns the assurance database (shared schema, `ass_` tables). Tenant-owned rows
are only reachable through a validated TenantContext and FAIL CLOSED otherwise
(see app/context.py and app/routing.py)."""
from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


url = getenv("DATABASE_URL", "sqlite:///./assurance.db")
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
