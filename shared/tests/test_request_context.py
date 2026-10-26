import unittest
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request

from isp_shared.request_context import (
    RequestContext,
    context_from_claims,
    get_request_context,
    install_request_context,
    internal_request_context,
)


def request() -> Request:
    item = Request({"type": "http", "headers": []})
    item.state.correlation_id = "correlation-1"
    return item


class RequestContextTests(unittest.TestCase):
    def test_tenant_claim_is_the_effective_scope(self):
        tenant_id, other_id = uuid4(), uuid4()
        item = request()
        context = context_from_claims(item, {
            "sub": "user-1",
            "tenant_id": str(tenant_id),
            "franchise_id": str(uuid4()),
            "permissions": ["crm.customer.view"],
            "roles": ["TENANT_ADMIN"],
            "branch_ids": [str(uuid4())],
        }, {tenant_id})
        self.assertEqual(context.resolve_tenant(None), tenant_id)
        self.assertEqual(context.correlation_id, "correlation-1")
        with self.assertRaises(HTTPException) as denied:
            context.resolve_tenant(other_id)
        self.assertEqual(denied.exception.status_code, 403)

    def test_multi_tenant_user_must_choose_an_allowed_tenant(self):
        first, second, denied_id = uuid4(), uuid4(), uuid4()
        context = context_from_claims(request(), {
            "sub": "user-2", "permissions": ["crm.customer.view"],
            "effective_access": {"tenant_ids": [str(first), str(second)]},
        }, {first, second})
        with self.assertRaises(HTTPException) as missing:
            context.resolve_tenant(None)
        self.assertEqual(missing.exception.status_code, 422)
        self.assertEqual(context.resolve_tenant(second), second)
        with self.assertRaises(HTTPException) as denied:
            context.resolve_tenant(denied_id)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HTTPException) as invalid:
            context.resolve_tenant("not-a-uuid")
        self.assertEqual(invalid.exception.status_code, 422)

    def test_platform_and_internal_contexts_are_explicitly_global(self):
        platform = context_from_claims(request(), {"sub": "admin", "permissions": ["*"]}, None)
        self.assertTrue(platform.platform_wide)
        self.assertIsNone(platform.resolve_tenant(None, allow_global=True))
        internal = internal_request_context(request(), "crm-service")
        self.assertTrue(internal.platform_wide)
        self.assertEqual(internal.authentication_type, "internal_service")

    def test_install_exposes_typed_and_transitional_context(self):
        item = request()
        context = RequestContext(
            user_id="user-3", tenant_id=None, franchise_id=None,
            branch_ids=frozenset(), permissions=frozenset({"*"}), roles=("PLATFORM_ADMIN",),
            correlation_id="correlation-1", allowed_tenant_ids=None,
        )
        install_request_context(item, context, "crm_principal")
        self.assertIs(get_request_context(item), context)
        self.assertEqual(item.state.crm_principal["subject"], "user-3")
        self.assertIsNone(item.state.crm_principal["allowed_tenant_ids"])


if __name__ == "__main__":
    unittest.main()
