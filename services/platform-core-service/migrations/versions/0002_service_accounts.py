"""Platform service accounts for narrowly scoped internal authentication."""
from alembic import op
import sqlalchemy as sa
revision = "0002_service_accounts"
down_revision = "0001_platform_auth"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("platform_service_accounts", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid()), sa.Column("name", sa.String(128), unique=True, nullable=False), sa.Column("key_hash", sa.String(64), unique=True, nullable=False), sa.Column("permissions", sa.Text(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("last_used_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
def downgrade(): op.drop_table("platform_service_accounts")
