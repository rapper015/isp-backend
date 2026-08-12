from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase,override_settings
from rest_framework.test import APIClient

from accounts.models import AdminUser
from common.passwords import hash_password
from customers.models import Customer,Franchise
from plans.models import Plan
from subscribers.models import Subscriber

from . import nas_services
from .models import FreeRadiusClient,NasAuditLog,NasDevice
from .routeros.base import RouterAuthenticationError,RouterConnectionRefused,RouterPermissionError,RouterTLSError
from .secrets import decrypt_secret,encrypt_secret,redact
from .security import UnsafeRouterAddress,validate_router_host


KEY=Fernet.generate_key().decode()


class FakeRouter:
    closed=False
    radius=[]
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def get_identity(self):return [{"name":"Main-CCR"}]
    def get_system_resources(self):return [{"version":"7.15","board-name":"CCR2004","architecture-name":"arm64","uptime":"1d","cpu-load":"4","free-memory":"1000","serial-number":"ABC"}]
    def get_interfaces(self):return [{"name":"ether1","running":"true"}]
    def get_ip_addresses(self):return [{"address":"10.0.0.1/24"}]
    def get_radius_servers(self):return list(self.radius)
    def get_ppp_aaa(self):return [{"use-radius":"no"}]
    def get_pppoe_servers(self):return []
    def get_hotspot_servers(self):return []
    def get_hotspot_profiles(self):return []
    def get_ip_pools(self):return [{"name":"pool1"}]
    def get_active_sessions(self):return []
    def preview_radius_changes(self,server,secret,services,port,coa_port=3799):return {"existing":self.radius[0] if self.radius else None,"desired":{"address":server,"service":",".join(services)},"action":"UPDATE" if self.radius else "CREATE","conflicts":[]}
    def configure_radius(self,server,secret,services,port,coa_port=3799):
        before=list(self.radius);self.radius[:]=[{".id":"*1","address":server,"service":",".join(services)}];return {"before":before,"after":list(self.radius),"verified":True}
    def disconnect_session(self,session_id):return []


class FailingRouter:
    def __init__(self,error):self.error=error;self.closed=False
    def __enter__(self):raise self.error
    def __exit__(self,*args):self.closed=True


@override_settings(NAS_ENCRYPTION_KEY=KEY,NAS_ALLOW_PRIVATE_NETWORKS=True,NAS_ALLOWED_NETWORKS=["10.0.0.0/8"],RADIUS_SERVER_IP="10.0.0.10")
class NasModuleTests(TestCase):
    def setUp(self):
        FakeRouter.radius=[]
        self.a=Franchise.objects.create(name="A",normalized_name="a");self.b=Franchise.objects.create(name="B",normalized_name="b")
        self.super=AdminUser.objects.create(name="S",email="s@test",password_hash=hash_password("x"),role="super_admin")
        self.noc=AdminUser.objects.create(name="N",email="n@test",password_hash=hash_password("x"),role="noc_admin");self.noc.franchises.add(self.a)
        self.payload={"confirm":True,"franchise_id":self.a.id,"name":"Router","short_name":"r1","host":"10.0.0.1","radius_source_ip":"10.0.0.2","api_port":8729,"api_protocol":"API_SSL","api_username":"app","api_password":"api-secret","radius_secret":"radius-secret","verify_tls":True,"connection_timeout":5,"radius_services":["ppp"]}

    def create(self,user=None):
        with mock.patch("network.nas_services._client",return_value=FakeRouter()):return nas_services.create_nas(self.payload,user or self.super)

    def test_successful_connection_v7_and_cleanup(self):
        router=FakeRouter()
        with mock.patch("network.nas_services._client",return_value=router):result=nas_services.test_connection(self.payload)
        self.assertTrue(result["authenticated"]);self.assertEqual(result["router"]["routeros_version"],"7.15");self.assertTrue(router.closed)

    def test_routeros_v6_response(self):
        router=FakeRouter();router.get_system_resources=lambda:[{"version":"6.49.17","architecture-name":"tile"}]
        with mock.patch("network.nas_services._client",return_value=router):result=nas_services.test_connection(self.payload)
        self.assertEqual(result["router"]["routeros_version"],"6.49.17")

    def test_unsupported_routeros_version(self):
        router=FakeRouter();router.get_system_resources=lambda:[{"version":"5.26"}]
        with mock.patch("network.nas_services._client",return_value=router),self.assertRaises(nas_services.NasServiceError) as caught:nas_services.test_connection(self.payload)
        self.assertEqual(caught.exception.code,"UNSUPPORTED_ROUTEROS_VERSION")

    def test_authentication_failure_is_safe(self):
        bad=FailingRouter(RouterAuthenticationError("bad credentials"))
        with mock.patch("network.nas_services._client",return_value=bad),self.assertRaises(nas_services.NasServiceError) as caught:nas_services.test_connection(self.payload)
        self.assertEqual(caught.exception.code,"AUTHENTICATION_FAILED")

    def test_wrong_port_permission_and_tls_failures_have_safe_codes(self):
        for error,code in ((RouterConnectionRefused("refused"),"CONNECTION_REFUSED"),(RouterPermissionError("denied"),"INSUFFICIENT_PERMISSION"),(RouterTLSError("bad cert"),"TLS_FAILED")):
            with self.subTest(code=code),mock.patch("network.nas_services._client",return_value=FailingRouter(error)),self.assertRaises(nas_services.NasServiceError) as caught:nas_services.test_connection(self.payload)
            self.assertEqual(caught.exception.code,code)

    def test_creation_encryption_freeradius_and_redaction(self):
        nas=self.create();self.assertNotIn("api-secret",nas.encrypted_api_password);self.assertEqual(decrypt_secret(nas.encrypted_api_password),"api-secret")
        self.assertTrue(FreeRadiusClient.objects.filter(nas=nas).exists());log=NasAuditLog.objects.get(action="CREATE");self.assertEqual(log.safe_request_data["api_password"],"[REDACTED]")

    def test_duplicate_nas_rejected(self):
        self.create()
        with self.assertRaises(nas_services.NasServiceError) as caught:self.create()
        self.assertEqual(caught.exception.code,"DUPLICATE_NAS")

    def test_tenant_isolation(self):
        nas=self.create();self.assertTrue(nas_services.scoped_nas({"userId":self.noc.id,"role":"noc_admin"}).filter(id=nas.id).exists())
        other=NasDevice.objects.create(franchise=self.b,name="B",nas_ip_address="10.1.0.1")
        self.assertFalse(nas_services.scoped_nas({"userId":self.noc.id,"role":"noc_admin"}).filter(id=other.id).exists())

    def test_preview_and_idempotent_radius_apply(self):
        nas=self.create()
        with mock.patch("network.nas_services._client",return_value=FakeRouter()):
            self.assertEqual(nas_services.radius_preview(nas)["action"],"CREATE");nas_services.configure_radius(nas,self.super,True);self.assertEqual(nas_services.radius_preview(nas)["action"],"UPDATE");nas_services.configure_radius(nas,self.super,True)
        self.assertEqual(len(FakeRouter.radius),1)

    def test_health_is_cached_and_get_does_not_poll(self):
        nas=self.create()
        with mock.patch("network.nas_services._client",return_value=FakeRouter()):health=nas_services.health_check(nas)
        nas.refresh_from_db();self.assertTrue(health["reachable"]);self.assertEqual(nas.cached_health,health)

    def test_sync_partial_failure_and_retry(self):
        nas=self.create()
        with mock.patch("network.nas_services._client",return_value=FailingRouter(RouterConnectionRefused("down"))),self.assertRaises(nas_services.NasServiceError):nas_services.sync_nas(nas,self.super)
        nas.refresh_from_db();self.assertEqual(nas.lifecycle_status,"ERROR")
        with mock.patch("network.nas_services._client",return_value=FakeRouter()):nas_services.sync_nas(nas,self.super)
        nas.refresh_from_db();self.assertEqual(nas.lifecycle_status,"ONLINE")

    def test_safe_delete_disables_referenced_nas(self):
        nas=self.create();plan=Plan.objects.create(plan_code="P",name="P",monthly_fee=1,speed_profile_name="p",download_rate_kbps=1,upload_rate_kbps=1);customer=Customer.objects.create(customer_code="C",full_name="C",phone="",address="",city="")
        Subscriber.objects.create(subscriber_code="S",customer=customer,plan=plan,username="u",password_hash="x",installation_address="",nas=nas)
        self.assertEqual(nas_services.safe_delete(nas,self.super),"disabled");nas.refresh_from_db();self.assertFalse(nas.enabled)

    def test_ssrf_blocks_loopback_and_outside_private_policy(self):
        with mock.patch("socket.getaddrinfo",return_value=[(2,1,6,"",("127.0.0.1",0))]),self.assertRaises(UnsafeRouterAddress):validate_router_host("localhost")
        with mock.patch("socket.getaddrinfo",return_value=[(2,1,6,"",("192.168.1.1",0))]),self.assertRaises(UnsafeRouterAddress):validate_router_host("router")

    def test_unauthorized_api(self):
        client=APIClient();self.assertEqual(client.get("/api/v1/nas/").status_code,401)

    def test_noc_api_tenant_scope_and_no_secret_output(self):
        nas=self.create();client=APIClient();client.force_authenticate(user={"userId":self.noc.id,"role":"noc_admin"});response=client.get("/api/v1/nas/")
        self.assertEqual(response.status_code,200);self.assertEqual(response.data[0]["id"],str(nas.public_id));self.assertNotIn("encrypted_api_password",response.data[0])

    def test_redact_nested_secrets(self):self.assertEqual(redact({"nested":{"radius_secret":"x"}})["nested"]["radius_secret"],"[REDACTED]")
