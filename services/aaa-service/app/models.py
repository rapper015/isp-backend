import uuid
from sqlalchemy import String
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class Credential(Base):
 __tablename__='credentials'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 subscriber_id:Mapped[uuid.UUID]=mapped_column(index=True)
 username:Mapped[str]=mapped_column(String(128),unique=True,index=True)
 password_hash:Mapped[str]=mapped_column(String(255))
 status:Mapped[str]=mapped_column(String(16),default='active')
class AdminUser(Base):
 __tablename__='admin_users'
 id:Mapped[uuid.UUID]=mapped_column(primary_key=True,default=uuid.uuid4)
 email:Mapped[str]=mapped_column(String(255),unique=True,index=True)
 password_hash:Mapped[str]=mapped_column(String(255))
 role:Mapped[str]=mapped_column(String(32),default='super_admin')
