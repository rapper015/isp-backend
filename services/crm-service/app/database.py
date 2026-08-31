"""CRM-owned persistence; this service never reads another service's tables."""

from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# SQLAlchemy's default PostgreSQL dialect expects psycopg2. The platform uses
# Psycopg 3, so make that driver explicit while retaining a simple local SQLite
# fallback for development.
DATABASE_URL = getenv("DATABASE_URL", "sqlite:///./crm.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
