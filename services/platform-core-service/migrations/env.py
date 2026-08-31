from alembic import context
from os import getenv
from app.database import Base
import app.models
config = context.config
database_url = getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
def run_migrations_offline():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    from sqlalchemy import create_engine
    connectable = create_engine(database_url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
