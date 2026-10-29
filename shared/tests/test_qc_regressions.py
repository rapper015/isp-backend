import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


def run_service_script(service: str, script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([
        str(ROOT / "shared" / "python"),
        str(ROOT / "services" / service),
    ])
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


class QcRegressionTests(unittest.TestCase):
    def test_nas_registration_rejects_control_syntax(self):
        result = run_service_script("aaa-service", r'''
            from uuid import uuid4
            from pydantic import ValidationError
            from app.schemas import NasDraftIn

            base = {
                "tenant_id": uuid4(), "name": "Central Router", "management_host": "192.0.2.10",
                "routeros_version": "7.19", "routeros_username": "isp-api",
                "routeros_password": "StrongPassword!23", "radius_source_ip": "192.0.2.10",
            }
            NasDraftIn.model_validate(base)
            for field in ("name", "short_name", "routeros_username", "routeros_password"):
                payload = {**base, field: "' OR '1'='1' --"}
                try:
                    NasDraftIn.model_validate(payload)
                    raise AssertionError(f"control syntax accepted in {field}")
                except ValidationError:
                    pass
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_franchise_onboarding_rejects_invalid_formats(self):
        result = run_service_script("crm-service", r'''
            from uuid import uuid4
            from pydantic import ValidationError
            from app.schemas import FranchiseOnboardingIn

            base = {
                "franchise_code": "NORTH-01", "name": "North Office", "access_template_id": uuid4(),
                "profile": {"address": "Main Road", "city": "Kolkata", "zip_code": "700001",
                    "mobile": "9876543210", "email": "office@example.com", "reseller_type": "BULK",
                    "currency_id": "INR", "sms_gateway": "OWN_SMS_GATEWAY"},
                "admin": {"full_name": "QA Admin", "username": "qa-admin", "email": "qa-admin@example.com",
                    "mobile": "9876543210", "password": "Password123!", "confirm_password": "Password123!"},
            }
            FranchiseOnboardingIn.model_validate(base)
            invalid = [
                {**base, "name": "' OR '1'='1' --"},
                {**base, "profile": {**base["profile"], "currency_id": "INR 232313"}},
                {**base, "profile": {**base["profile"], "mobile": "2" * 23}},
                {**base, "profile": {**base["profile"], "email": "f2@gmail.cip"}},
            ]
            for payload in invalid:
                try:
                    FranchiseOnboardingIn.model_validate(payload)
                    raise AssertionError(f"invalid franchise payload accepted: {payload}")
                except ValidationError:
                    pass
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_custom_roles_require_at_least_one_permission(self):
        result = run_service_script("platform-core-service", r'''
            from uuid import uuid4
            from pydantic import ValidationError
            from fastapi import HTTPException
            from app.main import resolved_organization_access
            from app.schemas import RoleCreateIn, RoleUpdateIn

            for schema, payload in (
                (RoleCreateIn, {"department_id": uuid4(), "name": "NOC", "code": "NOC", "permissions": []}),
                (RoleUpdateIn, {"permissions": []}),
            ):
                try:
                    schema.model_validate(payload)
                    raise AssertionError("empty permissions accepted")
                except ValidationError:
                    pass
            try:
                resolved_organization_access([])
                raise AssertionError("unassigned organization was accepted")
            except HTTPException as error:
                assert error.status_code == 403
                assert "not assigned to an active organization" in error.detail
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tenant_mobile_numbers_are_digits_with_bounded_length(self):
        result = run_service_script("tenancy-service", r'''
            from uuid import uuid4
            from pydantic import ValidationError
            from app.schemas import TenantOnboardingIn

            base = {
                "name": "QA tenant", "code": "QA-TENANT", "organization_type": "ISP",
                "business_email": "qa@example.test", "primary_mobile": "9876543210",
                "address": {}, "access_template_id": uuid4(),
                "admin": {
                    "full_name": "QA Admin", "username": "qa-admin",
                    "email": "qa-admin@example.test", "mobile": "9876543210",
                    "password": "Password123!", "confirm_password": "Password123!",
                },
            }
            TenantOnboardingIn.model_validate(base)
            for value in ("letters", "123456", "1" * 16):
                payload = {**base, "primary_mobile": value}
                try:
                    TenantOnboardingIn.model_validate(payload)
                    raise AssertionError(f"invalid mobile accepted: {value}")
                except ValidationError:
                    pass
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tenant_and_franchise_roles_exclude_platform_only_permissions(self):
        result = run_service_script("platform-core-service", r'''
            from app.main import PLATFORM_ONLY_PERMISSIONS, ROLE_PERMISSIONS

            assert ROLE_PERMISSIONS["TENANT_ADMIN"].isdisjoint(PLATFORM_ONLY_PERMISSIONS)
            assert ROLE_PERMISSIONS["FRANCHISE_ADMIN"].isdisjoint(PLATFORM_ONLY_PERMISSIONS)
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_plan_price_can_be_updated_without_unique_name_conflict(self):
        result = run_service_script("bss-service", r'''
            from decimal import Decimal
            from uuid import uuid4
            from sqlalchemy import create_engine, select
            from sqlalchemy.orm import Session

            from app.database import Base
            from app.main import PlanUpdate, _replace_plan_children
            from app.models import Plan, PlanPrice

            engine = create_engine("sqlite://")
            Base.metadata.create_all(engine, tables=[Plan.__table__, PlanPrice.__table__])
            with Session(engine) as session:
                plan = Plan(
                    plan_code="QC-PLAN", name="QC plan", tenant_id=uuid4(),
                    monthly_fee=Decimal("999"), download_rate_kbps=102400,
                    upload_rate_kbps=102400,
                )
                session.add(plan)
                session.flush()
                first = PlanUpdate(prices=[{
                    "name": "30 Days", "price": "999", "duration": 30,
                    "duration_unit": "DAY", "status": "ACTIVE",
                }])
                _replace_plan_children(session, plan, first)
                session.commit()

                second = PlanUpdate(prices=[{
                    "name": "30 Days", "price": "1099", "duration": 30,
                    "duration_unit": "DAY", "status": "ACTIVE",
                }])
                _replace_plan_children(session, plan, second)
                session.commit()
                rows = list(session.scalars(select(PlanPrice)))
                assert len(rows) == 1
                assert rows[0].price == Decimal("1099")
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
