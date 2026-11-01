"""Use a globally unique customer number for customer portal login."""
import sqlalchemy as sa
from alembic import op

revision = "0006_global_portal_login"
down_revision = "0005_customer_portal"
branch_labels = None
depends_on = None


def _unique_constraints(connection, table_name):
    return {item["name"] for item in sa.inspect(connection).get_unique_constraints(table_name)}


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

    customer_constraints = _unique_constraints(connection, "crm_customers")
    if "uq_crm_customer_tenant_number" in customer_constraints:
        op.drop_constraint("uq_crm_customer_tenant_number", "crm_customers", type_="unique")
    if "uq_crm_customer_number_global" not in customer_constraints:
        op.create_unique_constraint("uq_crm_customer_number_global", "crm_customers", ["customer_number"])

    portal_constraints = _unique_constraints(connection, "crm_customer_portal_identities")
    if "uq_crm_portal_username" in portal_constraints:
        op.drop_constraint("uq_crm_portal_username", "crm_customer_portal_identities", type_="unique")
    if "uq_crm_portal_login_global" not in portal_constraints:
        op.create_unique_constraint("uq_crm_portal_login_global", "crm_customer_portal_identities", ["username"])


def downgrade():
    connection = op.get_bind()
    portal_constraints = _unique_constraints(connection, "crm_customer_portal_identities")
    if "uq_crm_portal_login_global" in portal_constraints:
        op.drop_constraint("uq_crm_portal_login_global", "crm_customer_portal_identities", type_="unique")
    if "uq_crm_portal_username" not in portal_constraints:
        op.create_unique_constraint("uq_crm_portal_username", "crm_customer_portal_identities", ["tenant_id", "username"])

    customer_constraints = _unique_constraints(connection, "crm_customers")
    if "uq_crm_customer_number_global" in customer_constraints:
        op.drop_constraint("uq_crm_customer_number_global", "crm_customers", type_="unique")
    if "uq_crm_customer_tenant_number" not in customer_constraints:
        op.create_unique_constraint("uq_crm_customer_tenant_number", "crm_customers", ["tenant_id", "customer_number"])
