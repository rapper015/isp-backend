"""Add version-pinned BSS plan to AAA policy bindings."""
from alembic import op


revision = "0003_plan_network_policy_binding"
down_revision = "0002_bss_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.tables["bss_plan_network_policy_bindings"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table("bss_plan_network_policy_bindings")
