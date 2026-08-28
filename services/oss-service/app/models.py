import uuid
from datetime import datetime
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
class Subscriber(Base):
    __tablename__='subscribers'
    id: Mapped[uuid.UUID]=mapped_column(primary_key=True, default=uuid.uuid4)
    subscriber_code: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[uuid.UUID]=mapped_column(index=True) # CRM external reference
    plan_id: Mapped[uuid.UUID]=mapped_column(index=True) # BSS external reference
    username: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str]=mapped_column(String(255))
    service_type: Mapped[str]=mapped_column(String(16), default='pppoe')
    status: Mapped[str]=mapped_column(String(16), default='active')
    installation_address: Mapped[str]=mapped_column(String(255))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
class Order(Base):
    __tablename__='orders'
    id: Mapped[uuid.UUID]=mapped_column(primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    order_type: Mapped[str]=mapped_column(String(16))
    customer_id: Mapped[uuid.UUID]=mapped_column(index=True)
    subscriber_id: Mapped[uuid.UUID|None]=mapped_column(nullable=True, index=True)
    plan_id: Mapped[uuid.UUID|None]=mapped_column(nullable=True, index=True)
    status: Mapped[str]=mapped_column(String(16), default='pending')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
