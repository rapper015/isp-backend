"""Add Milestone 0 operator/user authentication tables.

Creates the aaa_users table from the SQLAlchemy metadata, following the
additive migration convention.
"""
from alembic import op

revision = "0007_auth_users"
down_revision = "0006_network_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.tables["aaa_users"].drop(bind=op.get_bind(), checkfirst=True)
