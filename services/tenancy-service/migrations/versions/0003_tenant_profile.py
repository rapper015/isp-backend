from alembic import op
import sqlalchemy as sa
revision = "0003_tenant_profile"
down_revision = "0002_tenancy_governance"
branch_labels = None
depends_on = None
def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ten_tenants")}
    if "profile" not in columns:
        op.add_column("ten_tenants", sa.Column("profile", sa.JSON(), nullable=False, server_default="{}"))
def downgrade(): op.drop_column("ten_tenants", "profile")
