"""add CRM-wide normalized phone ownership registry

Revision ID: 0007_global_phone_identities
Revises: 0006_global_customer_portal_login
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_global_phone_identities"
down_revision = "0006_global_portal_login"
branch_labels = None
depends_on = None


def _normalize(value):
    if not value:
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = f"91{digits}"
    return digits or None


def upgrade():
    op.create_table(
        "crm_phone_identities",
        sa.Column("normalized_phone", sa.String(32), primary_key=True),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_type", "owner_id", "field_name", name="uq_crm_phone_identity_owner_field"),
    )
    op.create_index("ix_crm_phone_identities_owner_type", "crm_phone_identities", ["owner_type"])
    op.create_index("ix_crm_phone_identities_owner_id", "crm_phone_identities", ["owner_id"])

    connection = op.get_bind()
    table = sa.table("crm_phone_identities",
        sa.column("normalized_phone", sa.String), sa.column("owner_type", sa.String),
        sa.column("owner_id", sa.Uuid), sa.column("field_name", sa.String))
    used = set()
    rows = []
    # Stable precedence preserves the earlier parent record when historical
    # duplicates exist: franchise -> branch -> customer -> contact -> lead.
    sources = [
        ("FRANCHISE", "crm_franchises", "profile", ("mobile", "landline", "whatsapp")),
        ("BRANCH", "crm_branches", "profile", ("mobile", "landline", "whatsapp")),
    ]
    for owner_type, table_name, profile_column, fields in sources:
        for owner_id, profile in connection.execute(sa.text(f"SELECT id, {profile_column} FROM {table_name} ORDER BY created_at, id")):
            profile = profile or {}
            for field in fields:
                normalized = _normalize(profile.get(field))
                if normalized and normalized not in used:
                    used.add(normalized)
                    rows.append({"normalized_phone": normalized, "owner_type": owner_type, "owner_id": owner_id, "field_name": field})
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    op.drop_index("ix_crm_phone_identities_owner_id", table_name="crm_phone_identities")
    op.drop_index("ix_crm_phone_identities_owner_type", table_name="crm_phone_identities")
    op.drop_table("crm_phone_identities")
