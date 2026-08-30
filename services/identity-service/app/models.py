"""Identity ORM models (`idp_` prefix)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class User(Base, Timestamped):
    """Application user (operator/admin/staff) who logs into the platform."""
    __tablename__ = "idp_users"
    __table_args__ = (UniqueConstraint("username_normalized", name="uq_idp_user_username"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="READ_ONLY", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)  # ACTIVE | DISABLED
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
