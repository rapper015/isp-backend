import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from pydantic import ValidationError

from isp_shared.contracts import ServiceContractError
from isp_shared.contracts.http import JsonServiceClient
from isp_shared.contracts.oss import CreateOrderCommand, OssProvisioningClient
from isp_shared.contracts.platform import CreateOrganizationUserCommand, PlatformClient


class Response:
    def __init__(self, body: dict | None = None):
        self.body = json.dumps(body).encode() if body is not None else b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class ServiceContractTests(unittest.TestCase):
    def test_transport_preserves_structured_upstream_error(self):
        body = io.BytesIO(json.dumps({
            "code": "NETWORK_RESOURCE_UNAVAILABLE",
            "message": "No DHCP pool is available.",
            "correlation_id": "corr-1",
            "retryable": False,
        }).encode())
        failure = HTTPError("http://oss/orders", 422, "invalid", {}, body)
        with patch("isp_shared.contracts.http.urlrequest.urlopen", side_effect=failure):
            with self.assertRaises(ServiceContractError) as raised:
                JsonServiceClient("OSS", "http://oss").request("POST", "/orders", {})
        error = raised.exception
        self.assertEqual(error.status_code, 422)
        self.assertEqual(error.code, "NETWORK_RESOURCE_UNAVAILABLE")
        self.assertEqual(error.correlation_id, "corr-1")
        self.assertFalse(error.retryable)

    def test_transport_marks_network_failures_retryable_without_leaking_exception(self):
        with patch("isp_shared.contracts.http.urlrequest.urlopen", side_effect=URLError("secret host detail")):
            with self.assertRaises(ServiceContractError) as raised:
                JsonServiceClient("Platform Core", "http://platform").request("GET", "/users")
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.code, "UPSTREAM_UNAVAILABLE")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.message, "Platform Core is unavailable")

    def test_oss_client_validates_commands_and_responses(self):
        tenant_id, order_id = uuid4(), uuid4()
        command = CreateOrderCommand(
            tenant_id=tenant_id,
            order_type="NEW_CONNECTION",
            customer_id="customer-1",
            idempotency_key="create-customer-1",
        )
        response = {
            "id": str(order_id), "tenant_id": str(tenant_id), "order_number": "ORD-1",
            "order_type": "NEW_CONNECTION", "state": "DRAFT", "customer_id": "customer-1",
        }
        with patch("isp_shared.contracts.http.urlrequest.urlopen", return_value=Response(response)) as call:
            created = OssProvisioningClient("http://oss", "key", "crm-service").create_order(command)
        self.assertEqual(created.id, order_id)
        sent = call.call_args.args[0]
        self.assertEqual(json.loads(sent.data)["tenant_id"], str(tenant_id))
        self.assertEqual(sent.headers["X-internal-api-key"], "key")
        with self.assertRaises(ValidationError):
            CreateOrderCommand.model_validate({**command.model_dump(), "unexpected": True})

        with patch("isp_shared.contracts.http.urlrequest.urlopen", return_value=Response({"id": str(order_id)})):
            with self.assertRaises(ServiceContractError) as incompatible:
                OssProvisioningClient("http://oss", "key", "crm-service").create_order(command)
        self.assertEqual(incompatible.exception.code, "UPSTREAM_CONTRACT_INVALID")
        self.assertFalse(incompatible.exception.retryable)

    def test_platform_client_sends_stable_idempotency_and_validates_user(self):
        tenant_id, franchise_id, user_id = uuid4(), uuid4(), uuid4()
        command = CreateOrganizationUserCommand(
            username="franchise_owner", password="Password123@", email="owner@example.com",
            full_name="Owner", tenant_id=tenant_id, franchise_id=franchise_id,
            roles=["FRANCHISE_ADMIN"], is_organization_owner=True,
        )
        with patch("isp_shared.contracts.http.urlrequest.urlopen", return_value=Response({
            "id": str(user_id), "username": "franchise_owner",
        })) as call:
            created = PlatformClient("http://platform", "Bearer token").create_organization_user(
                command, idempotency_key="franchise:1:admin"
            )
        self.assertEqual(str(created.id), str(user_id))
        sent = call.call_args.args[0]
        self.assertEqual(sent.headers["Idempotency-key"], "franchise:1:admin")
        self.assertNotIn("password", repr(created))


if __name__ == "__main__":
    unittest.main()
