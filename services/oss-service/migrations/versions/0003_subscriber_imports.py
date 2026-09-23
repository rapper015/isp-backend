"""Persistent subscriber CSV import batches and rows."""
from alembic import op

revision = "0003_subscriber_imports"
down_revision = "0002_oss_batch3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    op.drop_table("oss_subscriber_import_rows")
    op.drop_table("oss_subscriber_import_batches")
