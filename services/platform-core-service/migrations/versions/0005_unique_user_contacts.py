"""normalize and uniquely constrain operator email and mobile

Revision ID: 0005_unique_user_contacts
Revises: 0004_platform_user_franchise
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_unique_user_contacts"
down_revision = "0004_platform_user_franchise"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_users", sa.Column("email_normalized", sa.String(255), nullable=True))
    op.add_column("platform_users", sa.Column("mobile_normalized", sa.String(32), nullable=True))
    # Historical installations may already contain duplicates. Preserve those
    # accounts, but reserve each canonical value on the oldest matching row so
    # all future inserts are protected by the unique indexes.
    op.execute("""
        WITH ranked AS (
            SELECT id, lower(trim(email)) AS normalized,
                   row_number() OVER (PARTITION BY lower(trim(email)) ORDER BY created_at, id) AS position
            FROM platform_users WHERE email IS NOT NULL AND trim(email) <> ''
        )
        UPDATE platform_users AS users SET email_normalized = ranked.normalized
        FROM ranked WHERE users.id = ranked.id AND ranked.position = 1
    """)
    op.execute("""
        WITH ranked AS (
            SELECT id, regexp_replace(mobile, '[^0-9]', '', 'g') AS normalized,
                   row_number() OVER (PARTITION BY regexp_replace(mobile, '[^0-9]', '', 'g') ORDER BY created_at, id) AS position
            FROM platform_users WHERE mobile IS NOT NULL AND trim(mobile) <> ''
        )
        UPDATE platform_users AS users SET mobile_normalized = ranked.normalized
        FROM ranked WHERE users.id = ranked.id AND ranked.position = 1 AND ranked.normalized <> ''
    """)
    op.create_index("ix_platform_users_email_normalized", "platform_users", ["email_normalized"], unique=True)
    op.create_index("ix_platform_users_mobile_normalized", "platform_users", ["mobile_normalized"], unique=True)


def downgrade():
    op.drop_index("ix_platform_users_mobile_normalized", table_name="platform_users")
    op.drop_index("ix_platform_users_email_normalized", table_name="platform_users")
    op.drop_column("platform_users", "mobile_normalized")
    op.drop_column("platform_users", "email_normalized")
