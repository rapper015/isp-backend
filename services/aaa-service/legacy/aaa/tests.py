from django.test import TestCase

from common.passwords import hash_password
from customers.models import Customer
from plans.models import Plan
from subscribers.models import Subscriber

from . import services


class AuthenticateTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            customer_code="C-1",
            full_name="Test Customer",
            phone="0000000000",
            address="Test Address",
            city="Test City",
        )
        self.plan = Plan.objects.create(
            plan_code="P-1",
            name="Test Plan",
            monthly_fee=10,
            speed_profile_name="10M",
            download_rate_kbps=10240,
            upload_rate_kbps=10240,
        )
        self.subscriber = Subscriber.objects.create(
            subscriber_code="S-1",
            customer=self.customer,
            plan=self.plan,
            username="jdoe",
            password_hash=hash_password("secret"),
            service_type=Subscriber.ServiceType.PPPOE,
            installation_address="Test Address",
            mac_address="AA:BB:CC:DD:EE:FF",
        )

    def test_pppoe_password_auth_accepts_matching_service_type(self):
        response = services.authenticate(
            {
                "username": "jdoe",
                "password": "secret",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "pppoe",
            }
        )
        self.assertEqual(response["outcome"], "Access-Accept")
        self.assertEqual(response["authMethod"], "password")

    def test_hotspot_request_rejected_for_pppoe_subscriber(self):
        response = services.authenticate(
            {
                "username": "jdoe",
                "password": "secret",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "hotspot",
            }
        )
        self.assertEqual(response["outcome"], "Access-Reject")
        self.assertEqual(response["replyMessage"], "Subscriber is not provisioned for this service type")

    def test_wrong_password_still_rejected(self):
        response = services.authenticate(
            {
                "username": "jdoe",
                "password": "wrong",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "pppoe",
            }
        )
        self.assertEqual(response["outcome"], "Access-Reject")

    def test_mac_auth_accepts_known_device_regardless_of_password(self):
        response = services.authenticate(
            {
                "username": "aabbccddeeff",
                "password": "anything",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "mac",
                "callingStationId": "aa-bb-cc-dd-ee-ff",
            }
        )
        self.assertEqual(response["outcome"], "Access-Accept")
        self.assertEqual(response["authMethod"], "mac")

    def test_mac_auth_rejects_unknown_device(self):
        response = services.authenticate(
            {
                "username": "112233445566",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "mac",
                "callingStationId": "11:22:33:44:55:66",
            }
        )
        self.assertEqual(response["outcome"], "Access-Reject")

    def test_authorize_resolves_mac_authenticated_session(self):
        response = services.authorize(
            {
                "username": "aabbccddeeff",
                "nasIpAddress": "10.0.0.1",
                "serviceType": "mac",
                "callingStationId": "aa:bb:cc:dd:ee:ff",
            }
        )
        self.assertEqual(response["outcome"], "Access-Accept")
