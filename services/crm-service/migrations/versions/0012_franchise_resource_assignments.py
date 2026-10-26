"""Normalize franchise package, NAS and IP-pool entitlements.

Revision ID: 0012_franchise_resources
Revises: 0011_customer_onboarding
"""
import json
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision = "0012_franchise_resources"
down_revision = "0011_customer_onboarding"
branch_labels = None
depends_on = None


TABLES = (
    ("crm_franchise_package_assignments", "package_id", "package_ids", "uq_crm_franchise_package_assignment"),
    ("crm_franchise_nas_assignments", "nas_id", "nas_ids", "uq_crm_franchise_nas_assignment"),
    ("crm_franchise_ip_pool_assignments", "ip_pool_id", "ip_pool_ids", "uq_crm_franchise_ip_pool_assignment"),
)


def upgrade():
    for table, resource_column, _, unique_name in TABLES:
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("franchise_id", sa.Uuid(), nullable=False),
            sa.Column(resource_column, sa.Uuid(), nullable=False),
            sa.Column("created_by", sa.String(128)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["crm_tenants.id"]),
            sa.ForeignKeyConstraint(["franchise_id"], ["crm_franchises.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "franchise_id", resource_column, name=unique_name),
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_index(f"ix_{table}_franchise_id", table, ["franchise_id"])
        op.create_index(f"ix_{table}_{resource_column}", table, [resource_column])

    connection = op.get_bind()
    franchises = connection.execute(sa.text(
        "SELECT id, tenant_id, profile FROM crm_franchises"
    )).mappings()
    for franchise in franchises:
        try:
            tenant_id = UUID(str(franchise["tenant_id"]))
            franchise_id = UUID(str(franchise["id"]))
        except (TypeError, ValueError):
            # Existing foreign keys should make this impossible, but a malformed
            # legacy record must not prevent all tenants from migrating.
            continue
        profile = franchise["profile"] or {}
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except (TypeError, ValueError):
                profile = {}
        for table, resource_column, profile_key, _ in TABLES:
            valid_ids = set()
            for value in profile.get(profile_key, []):
                try:
                    valid_ids.add(UUID(str(value)))
                except (TypeError, ValueError):
                    continue
            assignment_table = sa.table(
                table,
                sa.column("id", sa.Uuid()),
                sa.column("tenant_id", sa.Uuid()),
                sa.column("franchise_id", sa.Uuid()),
                sa.column(resource_column, sa.Uuid()),
            )
            for resource_id in valid_ids:
                connection.execute(assignment_table.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    franchise_id=franchise_id,
                    **{resource_column: resource_id},
                ))


def downgrade():
    for table, resource_column, _, _ in reversed(TABLES):
        op.drop_index(f"ix_{table}_{resource_column}", table_name=table)
        op.drop_index(f"ix_{table}_franchise_id", table_name=table)
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_table(table)
