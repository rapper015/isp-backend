"""Add desired state and immutable NAS change plans."""
from alembic import op
revision = "0004_nas_desired_configuration"
down_revision = "0003_nas_secret_reveal"
branch_labels = None
depends_on = None

DESIRED_CONFIGURATION_TABLES = (
    "nas_desired_configurations",
    "nas_change_plans",
)

def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    tables = [Base.metadata.tables[name] for name in DESIRED_CONFIGURATION_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)

def downgrade() -> None:
    from app.database import Base
    for name in reversed(DESIRED_CONFIGURATION_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
