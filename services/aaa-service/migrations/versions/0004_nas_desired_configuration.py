"""Add desired state and immutable NAS change plans."""
from alembic import op
revision = "0004_nas_desired_configuration"
down_revision = "0003_nas_secret_reveal"
branch_labels = None
depends_on = None
def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())
def downgrade() -> None:
    from app.database import Base
    for name in ("nas_change_plans", "nas_desired_configurations"):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
