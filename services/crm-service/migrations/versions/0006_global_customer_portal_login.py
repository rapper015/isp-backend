"""Use a globally unique customer number for customer portal login."""
import sqlalchemy as sa
from alembic import op

revision = "0006_global_portal_login"
down_revision = "0005_customer_portal"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicate = connection.execute(sa.text("""
        SELECT upper(customer_number), count(*)
        FROM crm_customers
        GROUP BY upper(customer_number)
        HAVING count(*) > 1
        LIMIT 1
    """)).first()
    if duplicate:
        raise RuntimeError("Cannot enable global customer login: duplicate customer numbers exist")

    connection.execute(sa.text("""
        UPDATE crm_customer_portal_identities AS identity
        SET username = upper(customer.customer_number)
        FROM crm_customers AS customer
        WHERE customer.id = identity.customer_id
    """))

    op.drop_constraint("uq_crm_customer_tenant_number", "crm_customers", type_="unique")
    op.create_unique_constraint("uq_crm_customer_number_global", "crm_customers", ["customer_number"])
    op.drop_constraint("uq_crm_portal_username", "crm_customer_portal_identities", type_="unique")
    op.create_unique_constraint("uq_crm_portal_login_global", "crm_customer_portal_identities", ["username"])


def downgrade():
    op.drop_constraint("uq_crm_portal_login_global", "crm_customer_portal_identities", type_="unique")
    op.create_unique_constraint("uq_crm_portal_username", "crm_customer_portal_identities", ["tenant_id", "username"])
    op.drop_constraint("uq_crm_customer_number_global", "crm_customers", type_="unique")
    op.create_unique_constraint("uq_crm_customer_tenant_number", "crm_customers", ["tenant_id", "customer_number"])
