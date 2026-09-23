"""Persistent legacy invoice CSV import batches and rows."""
from alembic import op

revision = "0004_invoice_imports"
down_revision = "0003_plan_network_policy_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.tables["bss_invoice_import_batches"].create(bind=op.get_bind(), checkfirst=True)
    Base.metadata.tables["bss_invoice_import_rows"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table("bss_invoice_import_rows")
    op.drop_table("bss_invoice_import_batches")
