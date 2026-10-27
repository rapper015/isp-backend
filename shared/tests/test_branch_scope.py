import unittest
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from isp_shared.request_context import RequestContext
from isp_shared.tenancy import scope_session


class Base(DeclarativeBase):
    pass


class Branch(Base):
    __tablename__ = "test_branches"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(50))


class Customer(Base):
    __tablename__ = "test_customers"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("test_branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))


class Contact(Base):
    __tablename__ = "test_contacts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("test_customers.id"), index=True)
    value: Mapped[str] = mapped_column(String(50))


class BranchScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.tenant_id = uuid4()
        self.allowed_branch = uuid4()
        self.denied_branch = uuid4()
        self.allowed_customer = uuid4()
        self.denied_customer = uuid4()
        with Session(self.engine) as session:
            session.add_all([
                Branch(id=self.allowed_branch, tenant_id=self.tenant_id, name="Allowed"),
                Branch(id=self.denied_branch, tenant_id=self.tenant_id, name="Denied"),
                Customer(id=self.allowed_customer, tenant_id=self.tenant_id, branch_id=self.allowed_branch, name="Allowed"),
                Customer(id=self.denied_customer, tenant_id=self.tenant_id, branch_id=self.denied_branch, name="Denied"),
                Contact(id=uuid4(), tenant_id=self.tenant_id, customer_id=self.allowed_customer, value="allowed@example.test"),
                Contact(id=uuid4(), tenant_id=self.tenant_id, customer_id=self.denied_customer, value="denied@example.test"),
            ])
            session.commit()

    def scoped_session(self):
        session = Session(self.engine)
        scope_session(session, Base, RequestContext(
            user_id="branch-user",
            tenant_id=self.tenant_id,
            franchise_id=None,
            branch_ids=frozenset({self.allowed_branch}),
            permissions=frozenset({"crm.customer.view"}),
            roles=("BRANCH_OPERATOR",),
            correlation_id="test",
            allowed_tenant_ids=frozenset({self.tenant_id}),
        ))
        return session

    def test_direct_branch_owned_rows_are_filtered(self):
        with self.scoped_session() as session:
            self.assertEqual([item.id for item in session.scalars(select(Branch))], [self.allowed_branch])
            self.assertEqual([item.id for item in session.scalars(select(Customer))], [self.allowed_customer])
            self.assertIsNone(session.get(Customer, self.denied_customer))

    def test_branch_scope_propagates_to_descendants(self):
        with self.scoped_session() as session:
            contacts = list(session.scalars(select(Contact)))
            self.assertEqual(len(contacts), 1)
            self.assertEqual(contacts[0].customer_id, self.allowed_customer)


if __name__ == "__main__":
    unittest.main()
