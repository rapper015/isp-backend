"""CRM tenant aggregate. The tenant is the top-level isolation boundary."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Tenant(Base, Timestamped):
    __tablename__ = "crm_tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
