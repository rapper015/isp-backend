import uuid
from datetime import datetime
from sqlalchemy import DateTime,String,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class NasDevice(Base):
 __tablename__='nas_devices'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 name:Mapped[str]=mapped_column(String(128),unique=True)
 host:Mapped[str]=mapped_column(String(255),unique=True)
 status:Mapped[str]=mapped_column(String(16),default='unknown')
 last_checked_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class HealthObservation(Base):
 __tablename__='health_observations'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 nas_id:Mapped[uuid.UUID]=mapped_column(index=True)
 status:Mapped[str]=mapped_column(String(16))
 detail:Mapped[str|None]=mapped_column(String(255),nullable=True)
 observed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
