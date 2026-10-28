import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class OnboardingRetryTests(unittest.TestCase):
    def test_oss_activation_replay_returns_existing_workflow(self):
        script = textwrap.dedent(r'''
            from uuid import uuid4
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session

            from app.database import Base
            import app.models
            from app.models import Order, SagaInstance, Tenant
            from app import main

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            tenant_id, order_id = uuid4(), uuid4()
            with Session(engine, expire_on_commit=False) as session:
                session.add(Tenant(id=tenant_id, name='Tenant', code='TENANT-REPLAY'))
                order = Order(id=order_id, tenant_id=tenant_id, order_number='ORD-REPLAY',
                              order_type='NEW_CONNECTION', state='PROVISIONING')
                session.add(order); session.flush()
                saga = SagaInstance(tenant_id=tenant_id, order_id=order_id,
                                    workflow_type='NEW_CONNECTION', state='RUNNING')
                session.add(saga); session.commit()
                result = main.activate_order(order_id, session)
                assert result['workflow_id'] == str(saga.id)
                assert result['workflow_state'] == 'RUNNING'
                assert result['order_state'] == 'PROVISIONING'
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'oss-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_customer_partial_failure_keeps_local_records_and_resumes(self):
        script = textwrap.dedent(r'''
            import asyncio
            import json
            from unittest.mock import patch
            from uuid import uuid4
            from fastapi import HTTPException
            from sqlalchemy import create_engine, select, func
            from sqlalchemy.orm import Session
            from starlette.requests import Request

            from app.database import Base
            from app.models import Branch, Customer, CustomerOnboardingOperation, Franchise
            from isp_shared.contracts import ServiceContractError
            from isp_shared.contracts.oss import ActivationResult, OrderCreated
            from app import main

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            tenant_id, franchise_id, branch_id, plan_id = uuid4(), uuid4(), uuid4(), uuid4()
            order_id, subscription_id = uuid4(), uuid4()
            command = {
                'customer': {'full_name': 'Retry Customer', 'phone': '9876543210',
                             'franchise_id': str(franchise_id), 'branch_id': str(branch_id)},
                'address': {'address_type': 'INSTALLATION', 'city': 'Kolkata'},
                'order': {'requested_plan_reference': str(plan_id),
                          'franchise_id': str(franchise_id),
                          'idempotency_key': 'customer-retry-123456'},
            }
            request = Request({'type': 'http', 'headers': []})
            request.state.crm_principal = {'subject': str(uuid4()), 'tenant_id': str(tenant_id)}

            class OfflineClient:
                def __init__(self, *_args): pass
                def create_order(self, _payload): raise ServiceContractError('OSS', 502, 'OSS offline')

            class OnlineClient:
                def __init__(self, *_args): pass
                def create_order(self, payload):
                    assert payload.idempotency_key == 'customer-retry-123456'
                    return OrderCreated(id=order_id, tenant_id=tenant_id, order_number='ORD-1',
                                        order_type='NEW_CONNECTION', state='DRAFT')
                def activate_order(self, requested_order_id):
                    assert requested_order_id == str(order_id)
                    return ActivationResult(order_id=order_id, order_number='ORD-1',
                                            order_state='COMPLETED', workflow_state='COMPLETED',
                                            subscription_id=subscription_id)

            with Session(engine, expire_on_commit=False) as session:
                session.add_all([
                    Franchise(id=franchise_id, tenant_id=tenant_id, franchise_code='FR-1', name='Franchise'),
                    Branch(id=branch_id, tenant_id=tenant_id, franchise_id=franchise_id,
                           branch_code='BR-1', name='Branch', profile={'package_ids': [str(plan_id)]}),
                ])
                session.commit()
                with patch.object(main, 'OssProvisioningClient', OfflineClient):
                    try:
                        asyncio.run(main.onboard_customer(request, tenant_id, json.dumps(command), None, None, None, None, session))
                        raise AssertionError('expected failure')
                    except HTTPException as exc:
                        assert exc.status_code == 502
                operation = session.scalar(select(CustomerOnboardingOperation))
                assert operation.state == 'FAILED'
                assert operation.failed_step == 'ORDER_CREATE'
                assert session.scalar(select(func.count()).select_from(Customer)) == 1

                with patch.object(main, 'OssProvisioningClient', OnlineClient):
                    result = asyncio.run(main.onboard_customer(request, tenant_id, json.dumps(command), None, None, None, None, session))
                assert result['subscription_id'] == str(subscription_id)
                assert operation.state == 'COMPLETED'
                assert operation.attempts == 2
                assert session.scalar(select(func.count()).select_from(Customer)) == 1
                assert session.scalar(select(func.count()).select_from(CustomerOnboardingOperation)) == 1
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'crm-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_oss_create_order_honors_idempotency_key(self):
        script = textwrap.dedent(r'''
            from uuid import uuid4
            from sqlalchemy import create_engine, select, func
            from sqlalchemy.orm import Session
            from app.database import Base
            import app.models
            from app.models import Order
            from app.services.order_service import OrderService

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            tenant_id = uuid4()
            with Session(engine, expire_on_commit=False) as session:
                service = OrderService(session)
                first = service.create_order(tenant_id, order_type='NEW_CONNECTION',
                    customer_id='customer-1', idempotency_key='customer-onboarding:customer-1')
                session.commit()
                second = service.create_order(tenant_id, order_type='NEW_CONNECTION',
                    customer_id='customer-1', idempotency_key='customer-onboarding:customer-1')
                assert first.id == second.id
                assert session.scalar(select(func.count()).select_from(Order)) == 1
                try:
                    service.create_order(tenant_id, order_type='NEW_CONNECTION',
                        customer_id='different', idempotency_key='customer-onboarding:customer-1')
                    raise AssertionError('expected key conflict')
                except ValueError as exc:
                    assert 'different order' in str(exc)
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'oss-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_franchise_partial_failure_is_resumable(self):
        script = textwrap.dedent(r'''
            import json
            from unittest.mock import patch
            from urllib.error import URLError
            from uuid import uuid4
            from fastapi import HTTPException
            from sqlalchemy import create_engine, select, func
            from sqlalchemy.orm import Session
            from starlette.requests import Request

            from app.database import Base
            from app.models import Franchise
            from app.schemas import FranchiseOnboardingIn
            from app import main

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            tenant_id = uuid4()
            payload = FranchiseOnboardingIn(franchise_code='FR-RETRY', name='Retry Franchise',
                profile={'reseller_type': 'BULK', 'mobile': '9876543210',
                         'email': 'franchise@example.com', 'sms_gateway': 'OWN',
                         'address': 'Test address', 'city': 'Kolkata', 'zip_code': '700001',
                         'currency_id': 'INR'},
                admin={'full_name': 'Owner', 'username': 'fr_retry',
                       'email': 'fradmin@example.com', 'mobile': '9876543211',
                       'password': 'Password123@', 'confirm_password': 'Password123@'},
                access_template_id=uuid4())
            request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer test')]})
            request.state.crm_principal = {'subject': str(uuid4()), 'permissions': ['crm.franchise.manage'],
                                           'tenant_id': str(tenant_id)}
            keys = []

            def unavailable(req, timeout=10):
                keys.append(req.headers.get('Idempotency-key'))
                raise URLError('offline')

            with Session(engine, expire_on_commit=False) as session:
                with patch.object(main.urlrequest, 'urlopen', unavailable):
                    try:
                        main.onboard_franchise(tenant_id, payload, request, session)
                        raise AssertionError('expected failure')
                    except HTTPException as exc:
                        assert exc.status_code == 502
                item = session.scalar(select(Franchise))
                assert item.onboarding_state == 'FAILED'
                assert item.status == 'INACTIVE'

                class Response:
                    def __init__(self, body=b''): self.body = body
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def read(self): return self.body

                def available(req, timeout=10):
                    keys.append(req.headers.get('Idempotency-key'))
                    if req.full_url.endswith('/users'):
                        return Response(json.dumps({'id': 'admin-1', 'username': 'fr_retry'}).encode())
                    return Response()

                with patch.object(main.urlrequest, 'urlopen', available):
                    result = main.onboard_franchise(tenant_id, payload, request, session)
                assert result['onboarding_state'] == 'COMPLETED'
                assert result['status'] == 'ACTIVE'
                assert session.scalar(select(func.count()).select_from(Franchise)) == 1
                stable = f'franchise-onboarding:{item.id}:admin'
                assert [key for key in keys if key] == [stable, stable]
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'crm-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_platform_user_idempotency_replays_and_rejects_key_reuse(self):
        script = textwrap.dedent(r'''
            import os
            from unittest.mock import patch
            from uuid import uuid4
            from fastapi import HTTPException
            from sqlalchemy import create_engine, select, func
            from sqlalchemy.orm import Session
            from starlette.requests import Request

            os.environ['PLATFORM_JWT_SECRET'] = 'x' * 40
            from app.database import Base
            from app.models import PlatformUser, Role
            from app.schemas import UserCreateIn
            from app import main

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            tenant_id, actor_id = uuid4(), uuid4()
            payload = UserCreateIn(username='retry_admin', password='Password123@',
                email='retry@example.com', tenant_id=tenant_id, roles=['TENANT_ADMIN'],
                is_organization_owner=True)
            request = Request({'type': 'http', 'headers': [(b'idempotency-key', b'onboarding-key-123456')]})
            claims = {'sub': str(actor_id), 'permissions': ['*']}
            with Session(engine, expire_on_commit=False) as session:
                session.add(Role(name='TENANT_ADMIN', global_role=True)); session.commit()
                with patch.object(main, 'require_permission', return_value=claims), \
                     patch.object(main, 'hash_password', return_value='secure-hash'):
                    first = main.create_user(payload, request, session)
                    second = main.create_user(payload, request, session)
                    assert first['id'] == second['id']
                    assert session.scalar(select(func.count()).select_from(PlatformUser)) == 1
                    changed = payload.model_copy(update={'email': 'different@example.com'})
                    try:
                        main.create_user(changed, request, session)
                        raise AssertionError('expected idempotency conflict')
                    except HTTPException as exc:
                        assert exc.status_code == 409
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'platform-core-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_failure_is_persisted_and_retry_reuses_identity(self):
        script = textwrap.dedent(r'''
            from unittest.mock import patch
            from uuid import uuid4
            from fastapi import HTTPException
            from sqlalchemy import create_engine, select, func
            from sqlalchemy.orm import Session
            from starlette.requests import Request

            from app.database import Base
            from app.models import Tenant, TenantDomain
            from app.schemas import TenantOnboardingIn
            from app import main

            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            payload = TenantOnboardingIn(
                name='Retry ISP', code='RETRY-ISP', organization_type='ISP',
                business_email='owner@example.com', primary_mobile='9876543210',
                status='ACTIVE', custom_domain='retry.example.com', address={'city': 'Kolkata'},
                admin={'full_name': 'Owner', 'username': 'retry_owner',
                       'email': 'owner@example.com', 'mobile': '9876543210',
                       'password': 'Password123@', 'confirm_password': 'Password123@'},
                access_template_id=uuid4())
            request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer test')]})
            request.state.tenancy_principal = {'subject': str(uuid4())}
            calls = []

            def first_platform(path, request, **kwargs):
                calls.append(kwargs.get('idempotency_key'))
                raise HTTPException(503, 'platform unavailable')

            with Session(engine, expire_on_commit=False) as session:
                with patch.object(main, '_sync_aaa_tenant'), patch.object(main, '_platform_request', first_platform):
                    try:
                        main.onboard_tenant(payload, request, session)
                        raise AssertionError('expected partial failure')
                    except HTTPException as exc:
                        assert exc.status_code == 503
                tenant = session.scalar(select(Tenant))
                assert tenant is not None
                assert tenant.profile['onboarding']['state'] == 'FAILED'
                assert tenant.profile['onboarding']['failed_step'] == 'ADMIN_CREATE'

                def successful_platform(path, request, **kwargs):
                    calls.append(kwargs.get('idempotency_key'))
                    if path.endswith('/users'):
                        return {'id': 'admin-1', 'username': 'retry_owner'}
                    return {}

                def provision(db, tenant_id, **kwargs):
                    item = db.get(Tenant, tenant_id)
                    item.status = 'ACTIVE'; item.provision_state = 'ACTIVE'
                    return item

                with patch.object(main, '_sync_aaa_tenant'), \
                     patch.object(main, '_platform_request', successful_platform), \
                     patch.object(main.tenant_service, 'provision_tenant', provision):
                    result = main.onboard_tenant(payload, request, session)
                assert result['onboarding_state'] == 'COMPLETED'
                assert session.scalar(select(func.count()).select_from(Tenant)) == 1
                assert session.scalar(select(func.count()).select_from(TenantDomain)) == 1
                stable = f'tenant-onboarding:{tenant.id}:admin'
                assert [value for value in calls if value] == [stable, stable]
        ''')
        env = os.environ.copy()
        paths = [str(ROOT / 'shared' / 'python'), str(ROOT / 'services' / 'tenancy-service')]
        env['PYTHONPATH'] = os.pathsep.join(paths)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
