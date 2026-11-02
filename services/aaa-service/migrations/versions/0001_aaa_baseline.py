"""AAA schema baseline.

Only tables owned by this revision are created.  Using the complete live
metadata here would pre-create objects introduced by later migrations.
"""
from alembic import op

revision = "0001_aaa_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_TABLES = (
    "aaa_tenants",
    "aaa_credentials",
    "radius_server_groups",
    "aaa_nas",
    "radius_servers",
    "aaa_accounting_events",
    "aaa_sessions",
    "aaa_outbox",
    "aaa_consumer_inbox",
    "aaa_usage",
    "aaa_radius_commands",
    "aaa_ip_pools",
    "aaa_ip_leases",
    "aaa_audit_log",
)

def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    tables = [Base.metadata.tables[name] for name in BASELINE_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)

def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    tables = [Base.metadata.tables[name] for name in BASELINE_TABLES]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables)
