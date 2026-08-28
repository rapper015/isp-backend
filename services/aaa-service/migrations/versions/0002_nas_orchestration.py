"""Add NAS orchestration persistence tables.

The baseline migration intentionally derives its schema from SQLAlchemy metadata.
This additive migration follows that established convention so installations
already at the baseline receive the newly introduced NAS tables safely.
"""
from alembic import op

revision = "0002_nas_orchestration"
down_revision = "0001_aaa_baseline"
branch_labels = None
depends_on = None

def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())

def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in ("nas_configuration_jobs", "nas_configuration_snapshots", "nas_radius_assignments", "nas_credentials"):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
