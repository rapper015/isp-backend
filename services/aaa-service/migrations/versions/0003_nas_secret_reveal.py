"""Add one-time NAS secret reveal records."""
from alembic import op
import sqlalchemy as sa

revision = "0003_nas_secret_reveal"
down_revision = "0002_nas_orchestration"
branch_labels = None
depends_on = None
def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("nas_secret_reveals"):
        return
    op.create_table(
        "nas_secret_reveals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Uuid(),
            sa.ForeignKey("nas_radius_assignments.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_nas_secret_reveals_assignment_id",
        "nas_secret_reveals",
        ["assignment_id"],
    )

def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("nas_secret_reveals"):
        op.drop_table("nas_secret_reveals")
