import uuid
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
class IPPool(Base):
 __tablename__='ip_pools'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 pool_code:Mapped[str]=mapped_column(String(64),unique=True)
 network_cidr:Mapped[str]=mapped_column(String(64))
class IPAddress(Base):
 __tablename__='ip_addresses'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 pool_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('ip_pools.id'))
 address:Mapped[str]=mapped_column(String(64),unique=True,index=True)
 status:Mapped[str]=mapped_column(String(16),default='available')
 subscriber_id:Mapped[uuid.UUID|None]=mapped_column(nullable=True,index=True)
