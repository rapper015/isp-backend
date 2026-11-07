"""Add Workforce expert assistance and advanced field-operations storage.

Revision ID: 0002_workforce_advanced_ops
Revises: 0001
"""
from alembic import op


revision = "0002_workforce_advanced_ops"
down_revision = "0001"
branch_labels = None
depends_on = None


ADVANCED_TABLES = (
    "workforce_expert_session",
    "workforce_failure_visualization",
    "workforce_equipment_overlay",
    "workforce_spare_part",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in ADVANCED_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    for name in reversed(ADVANCED_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
