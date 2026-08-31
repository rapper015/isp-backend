from os import getenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase,sessionmaker
u=getenv('DATABASE_URL','sqlite:///./nms.db')
if u.startswith('postgresql://'):u=u.replace('postgresql://','postgresql+psycopg://',1)
engine=create_engine(u,pool_pre_ping=True);SessionLocal=sessionmaker(bind=engine,autocommit=False,autoflush=False)
class Base(DeclarativeBase):pass
