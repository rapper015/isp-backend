"""canonicalize legacy Indian national phone identities

Revision ID: 0008_canonical_indian_phones
Revises: 0007_global_phone_identities
"""
from alembic import op


revision = "0008_canonical_indian_phones"
down_revision = "0007_global_phone_identities"
branch_labels = None
depends_on = None


def upgrade():
    # The registry was introduced immediately before this migration. Prefix
    # legacy 10-digit national values before new writes are accepted.
    op.execute("""
        UPDATE crm_phone_identities
        SET normalized_phone = '91' || normalized_phone
        WHERE length(normalized_phone) = 10
    """)


def downgrade():
    op.execute("""
        UPDATE crm_phone_identities
        SET normalized_phone = substring(normalized_phone from 3)
        WHERE length(normalized_phone) = 12 AND normalized_phone LIKE '91%'
    """)
