from os import getenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
url = getenv('DATABASE_URL', 'sqlite:///./oss.db')
if url.startswith('postgresql://'): url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
class Base(DeclarativeBase): pass
