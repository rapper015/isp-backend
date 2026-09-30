from alembic import op
import sqlalchemy as sa
revision = "0003_platform_user_mobile"
down_revision = "0002_service_accounts"
branch_labels = None
depends_on = None
def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("platform_users")}
    if "mobile" not in columns:
        op.add_column("platform_users", sa.Column("mobile", sa.String(32), nullable=True))
def downgrade(): op.drop_column("platform_users", "mobile")
