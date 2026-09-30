from alembic import op
import sqlalchemy as sa
revision = "0004_remove_tenant_features"
down_revision = "0003_tenant_profile"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "ten_tenant_features" in tables:
        op.drop_table("ten_tenant_features")
    if "ten_feature_flags" in tables:
        op.drop_table("ten_feature_flags")
    columns = {column["name"] for column in inspector.get_columns("ten_tenants")}
    if "feature_flags_ref" in columns:
        op.drop_column("ten_tenants", "feature_flags_ref")

def downgrade():
    raise RuntimeError("Tenant feature access was intentionally removed and cannot be restored automatically")
