"""enforce unique platform department codes

Revision ID: 0008_department_codes
Revises: 0007_department_scopes
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_department_codes"
down_revision = "0007_department_scopes"
branch_labels = None
depends_on = None

def upgrade():
    op.create_index(
        "uq_platform_department_platform_code",
        "platform_departments",
        ["code"],
        unique=True,
        postgresql_where=sa.text("scope = 'PLATFORM' AND tenant_id IS NULL"),
    )

def downgrade():
    op.drop_index("uq_platform_department_platform_code", table_name="platform_departments")
