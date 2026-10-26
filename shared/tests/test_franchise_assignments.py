import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FranchiseAssignmentTests(unittest.TestCase):
    def run_crm_script(self, source: str) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT / "shared" / "python"),
            str(ROOT / "services" / "crm-service"),
        ])
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assignments_are_relational_and_replaceable(self):
        self.run_crm_script(r'''
            from uuid import uuid4
            from sqlalchemy import create_engine, func, select
            from sqlalchemy.orm import Session

            from app.database import Base
            import app.models
            from app.models import (
                Franchise, FranchiseIpPoolAssignment, FranchiseNasAssignment,
                FranchisePackageAssignment, Tenant,
            )
            from app.services.franchise_assignment_service import (
                assignments_for, profile_with_assignments, replace_assignments,
            )

            engine = create_engine("sqlite://")
            Base.metadata.create_all(engine)
            tenant_id, franchise_id = uuid4(), uuid4()
            package_a, package_b, nas_id, pool_id = uuid4(), uuid4(), uuid4(), uuid4()
            with Session(engine, expire_on_commit=False) as session:
                session.add(Tenant(id=tenant_id, name="Tenant"))
                franchise = Franchise(
                    id=franchise_id, tenant_id=tenant_id, franchise_code="FR-1",
                    name="Franchise", profile={"city": "Kolkata"},
                )
                session.add(franchise); session.flush()
                replace_assignments(session, franchise, {
                    "city": "Kolkata",
                    "package_ids": [package_b, package_a, package_a],
                    "nas_ids": [nas_id],
                    "ip_pool_ids": [pool_id],
                }, "tester")
                session.commit()

                values = assignments_for(session, franchise)
                assert values["package_ids"] == sorted([str(package_a), str(package_b)])
                assert values["nas_ids"] == [str(nas_id)]
                assert values["ip_pool_ids"] == [str(pool_id)]
                assert profile_with_assignments(franchise.profile, values)["city"] == "Kolkata"
                assert session.scalar(select(func.count()).select_from(FranchisePackageAssignment)) == 2
                assert session.scalar(select(func.count()).select_from(FranchiseNasAssignment)) == 1
                assert session.scalar(select(func.count()).select_from(FranchiseIpPoolAssignment)) == 1

                # Replaying an unchanged profile must not violate the unique
                # constraints or create duplicate entitlement rows.
                replace_assignments(session, franchise, franchise.profile, "tester")
                session.commit()
                assert session.scalar(select(func.count()).select_from(FranchisePackageAssignment)) == 2

                replacement = uuid4()
                replace_assignments(session, franchise, {
                    "city": "Kolkata", "package_ids": [replacement],
                    "nas_ids": [], "ip_pool_ids": [],
                }, "tester")
                session.commit()
                values = assignments_for(session, franchise)
                assert values == {
                    "package_ids": [str(replacement)], "nas_ids": [], "ip_pool_ids": [],
                }
                assert franchise.profile["package_ids"] == [str(replacement)]
                assert franchise.profile["nas_ids"] == []
                assert franchise.profile["ip_pool_ids"] == []
        ''')

    def test_migration_backfills_legacy_profile_arrays(self):
        self.run_crm_script(r'''
            import importlib.util
            from pathlib import Path
            from uuid import uuid4
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            from sqlalchemy import Column, JSON, MetaData, String, Table, Uuid, create_engine, select

            root = Path.cwd()
            migration_path = root / "services" / "crm-service" / "migrations" / "versions" / "0012_franchise_resource_assignments.py"
            spec = importlib.util.spec_from_file_location("crm_assignment_migration", migration_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)

            engine = create_engine("sqlite://")
            metadata = MetaData()
            tenants = Table("crm_tenants", metadata, Column("id", Uuid, primary_key=True))
            franchises = Table(
                "crm_franchises", metadata,
                Column("id", Uuid, primary_key=True),
                Column("tenant_id", Uuid, nullable=False),
                Column("profile", JSON, nullable=False),
            )
            metadata.create_all(engine)
            tenant_id, franchise_id = uuid4(), uuid4()
            package_id, nas_id, pool_id = uuid4(), uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(tenants.insert().values(id=tenant_id))
                connection.execute(franchises.insert().values(
                    id=franchise_id, tenant_id=tenant_id,
                    profile={
                        "package_ids": [str(package_id), str(package_id), "invalid"],
                        "nas_ids": [str(nas_id)], "ip_pool_ids": [str(pool_id)],
                    },
                ))
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
                reflected = MetaData()
                reflected.reflect(connection)
                assert len(connection.execute(select(reflected.tables["crm_franchise_package_assignments"])).all()) == 1
                assert len(connection.execute(select(reflected.tables["crm_franchise_nas_assignments"])).all()) == 1
                assert len(connection.execute(select(reflected.tables["crm_franchise_ip_pool_assignments"])).all()) == 1
        ''')

    def test_aaa_uses_normalized_crm_assignment_endpoint(self):
        source = r'''
            from types import SimpleNamespace
            from unittest.mock import patch
            from uuid import uuid4
            from starlette.requests import Request

            from app.main import franchise_resource_ids

            tenant_id, franchise_id, nas_id = uuid4(), uuid4(), uuid4()
            request = Request({"type": "http", "headers": []})
            request.state.aaa_principal = {"franchise_id": str(franchise_id)}
            response = SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"nas_ids": [str(nas_id)]},
            )
            with patch("app.main.httpx.get", return_value=response) as call:
                assert franchise_resource_ids(request, tenant_id, "nas") == {nas_id}
                assert call.call_args.args[0].endswith(
                    f"/api/crm/franchises/{franchise_id}/resource-assignments"
                )
        '''
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT / "shared" / "python"),
            str(ROOT / "services" / "aaa-service"),
        ])
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
