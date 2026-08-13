import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aaa.models import AccountingRecord, ActiveSession
from accounts.models import AdminUser
from customers.franchises import resolve_franchise

from .models import FreeRadiusClient, NasAuditLog, NasDevice
from .routeros import MikroTikRouterClient
from .routeros.base import RouterError
from .secrets import decrypt_secret, encrypt_secret, redact
from .security import UnsafeRouterAddress, validate_router_host

logger=logging.getLogger(__name__)


class NasServiceError(Exception):
    def __init__(self,code,message,status=400): self.code=code;self.message=message;self.status=status;super().__init__(message)


def normalize_router_data(value):
    if isinstance(value,list):return [normalize_router_data(item) for item in value]
    if isinstance(value,dict):
        return {("id" if key==".id" else key.replace("-","_")):normalize_router_data(item) for key,item in value.items()}
    return value


def validate_routeros_version(resources):
    version=(resources or {}).get("version","")
    if not version:raise NasServiceError("MALFORMED_RESPONSE","RouterOS resource response did not include a version",502)
    if not (version.startswith("6.") or version.startswith("7.")):raise NasServiceError("UNSUPPORTED_ROUTEROS_VERSION",f"RouterOS version {version} is not supported",422)


def scoped_nas(user_payload):
    qs=NasDevice.objects.select_related("franchise").filter(deleted_at__isnull=True)
    if user_payload.get("role")=="super_admin": return qs
    admin=AdminUser.objects.filter(id=user_payload.get("userId"),is_active=True).first()
    if not admin:return qs.none()
    scope=Q(franchise__admin_users=admin)
    if admin.franchise_id:scope|=Q(franchise__reseller_franchise_id=admin.franchise_id)
    return qs.filter(scope).distinct()


def get_nas(public_id,user_payload):
    nas=scoped_nas(user_payload).filter(public_id=public_id).first()
    if not nas: raise NasServiceError("NAS_NOT_FOUND","NAS device not found",404)
    return nas


def _client(config,stored=False):
    try: validate_router_host(config.nas_ip_address if stored else config["host"])
    except UnsafeRouterAddress as exc: raise NasServiceError(str(exc),"Router address is not allowed") from exc
    protocol=config.api_protocol if stored else config["api_protocol"]
    verify=config.verify_tls if stored else config.get("verify_tls",True)
    if protocol=="API_SSL" and not verify and not settings.NAS_ALLOW_INSECURE_TLS:
        raise NasServiceError("INSECURE_TLS_DISABLED","TLS verification cannot be disabled in this deployment")
    return MikroTikRouterClient(
        host=config.nas_ip_address if stored else config["host"],port=config.api_port if stored else config["api_port"],
        username=config.api_username if stored else config["api_username"],password=decrypt_secret(config.encrypted_api_password) if stored else config["api_password"],
        use_ssl=protocol=="API_SSL",verify_tls=verify,timeout=config.connection_timeout if stored else config.get("connection_timeout",5),
        ca_certificate=config.ca_certificate if stored else config.get("ca_certificate"),certificate_fingerprint=config.certificate_fingerprint if stored else config.get("certificate_fingerprint","")
    )


def _safe_router_call(callback):
    try:return callback()
    except RouterError as exc: raise NasServiceError(exc.code,str(exc),502) from exc


def test_connection(config):
    def run():
        with _client(config) as client:
            identity=client.get_identity();resources=client.get_system_resources()
            identity=identity[0] if identity else {};resources=resources[0] if resources else {}
            validate_routeros_version(resources)
            return {"success":True,"reachable":True,"authenticated":True,"router":{"identity":identity.get("name",""),"routeros_version":resources.get("version",""),"board_name":resources.get("board-name","") or resources.get("platform",""),"architecture":resources.get("architecture-name",""),"uptime":resources.get("uptime","")},"capabilities":{"radius":True,"pppoe":True,"hotspot":True,"api_write":True},"warnings":[]}
    return _safe_router_call(run)


def test_connection_stored(nas):
    def run():
        with _client(nas,stored=True) as client:
            identity=client.get_identity();resources=client.get_system_resources()
            validate_routeros_version(resources[0] if resources else {})
            return {"success":True,"reachable":True,"authenticated":True,"router":{"identity":identity[0].get("name","") if identity else "","routeros_version":resources[0].get("version","") if resources else ""},"warnings":[]}
    return _safe_router_call(run)


def discover(config=None,nas=None):
    def run():
        with _client(nas or config,stored=bool(nas)) as client:
            identity=client.get_identity();resources=client.get_system_resources()
            validate_routeros_version(resources[0] if resources else {})
            return normalize_router_data({"router":{**(resources[0] if resources else {}),"identity":(identity[0].get("name","") if identity else "")},"interfaces":client.get_interfaces(),"ip_addresses":client.get_ip_addresses(),"radius":client.get_radius_servers(),"ppp_aaa":client.get_ppp_aaa(),"pppoe_servers":client.get_pppoe_servers(),"hotspot_servers":client.get_hotspot_servers(),"ip_pools":client.get_ip_pools()})
    return _safe_router_call(run)


def audit(nas,user,action,status,safe_data=None,before=None,after=None,error=None):
    return NasAuditLog.objects.create(nas=nas,franchise=nas.franchise,user=user,action=action,status=status,safe_request_data=redact(safe_data or {}),previous_configuration=redact(before or {}),new_configuration=redact(after or {}),error_code=getattr(error,"code","") if error else "",error_message=str(error)[:500] if error else "")


def sync_freeradius(nas):
    if not nas.radius_source_ip: raise NasServiceError("RADIUS_SOURCE_IP_REQUIRED","RADIUS source IP is required")
    client,_=FreeRadiusClient.objects.update_or_create(nas=nas,defaults={"franchise":nas.franchise,"source_ip":nas.radius_source_ip,"short_name":nas.short_name or nas.name[:64],"nas_type":nas.nas_type,"secret_encrypted":nas.radius_secret_encrypted,"enabled":nas.enabled})
    client.verified_at=timezone.now();client.save(update_fields=["verified_at"]);return client


@transaction.atomic
def create_nas(data,user):
    franchise=resolve_franchise(data["franchise_id"])
    if not franchise: raise NasServiceError("FRANCHISE_NOT_FOUND","Franchise not found")
    if user.role!="super_admin" and user.franchise_id!=franchise.reseller_franchise_id and not user.franchises.filter(id=franchise.id).exists(): raise NasServiceError("TENANT_ACCESS_DENIED","Franchise access denied",403)
    if NasDevice.objects.filter(franchise=franchise,nas_ip_address=data["host"],deleted_at__isnull=True).exists(): raise NasServiceError("DUPLICATE_NAS","A NAS with this management host already exists",409)
    if NasDevice.objects.filter(franchise=franchise,radius_source_ip=data["radius_source_ip"],deleted_at__isnull=True).exists(): raise NasServiceError("DUPLICATE_RADIUS_SOURCE","A NAS with this RADIUS source IP already exists",409)
    discovery=discover(config=data)
    router=discovery["router"]
    nas=NasDevice.objects.create(franchise=franchise,name=data["name"],short_name=data.get("short_name",data["name"][:64]),description=data.get("description",""),vendor="MIKROTIK",nas_type=data.get("nas_type","mikrotik"),nas_ip_address=data["host"],radius_source_ip=data["radius_source_ip"],api_port=data["api_port"],api_protocol=data["api_protocol"],api_username=data["api_username"],encrypted_api_password=encrypt_secret(data["api_password"]),radius_secret_encrypted=encrypt_secret(data["radius_secret"]),radius_auth_port=data.get("radius_auth_port",1812),radius_accounting_port=data.get("radius_accounting_port",1813),coa_port=data.get("coa_port",3799),routeros_version=router.get("version",""),architecture=router.get("architecture_name",""),board_name=router.get("board_name","") or router.get("platform",""),serial_number=router.get("serial_number",""),system_identity=router.get("identity",""),lifecycle_status="ONLINE",last_connection_at=timezone.now(),connection_timeout=data.get("connection_timeout",5),verify_tls=data.get("verify_tls",True),certificate_fingerprint=data.get("certificate_fingerprint",""),ca_certificate=data.get("ca_certificate",""),selected_radius_services=data.get("radius_services",[]),discovered_data=discovery,created_by=user,updated_by=user)
    sync_freeradius(nas);audit(nas,user,"CREATE","SUCCESS",data,after={"public_id":str(nas.public_id),"discovery":discovery});return nas


def update_nas(nas,data,user):
    before={"name":nas.name,"host":nas.nas_ip_address,"enabled":nas.enabled}
    mapping={"name":"name","short_name":"short_name","description":"description","host":"nas_ip_address","radius_source_ip":"radius_source_ip","api_port":"api_port","api_protocol":"api_protocol","api_username":"api_username","verify_tls":"verify_tls","connection_timeout":"connection_timeout","certificate_fingerprint":"certificate_fingerprint","ca_certificate":"ca_certificate","enabled":"enabled","radius_services":"selected_radius_services"}
    for source,target in mapping.items():
        if source in data:setattr(nas,target,data[source])
    if data.get("api_password"):nas.encrypted_api_password=encrypt_secret(data["api_password"])
    if data.get("radius_secret"):nas.radius_secret_encrypted=encrypt_secret(data["radius_secret"])
    nas.updated_by=user;nas.save();sync_freeradius(nas);audit(nas,user,"UPDATE","SUCCESS",data,before=before,after={"name":nas.name,"host":nas.nas_ip_address,"enabled":nas.enabled});return nas


def sync_nas(nas,user):
    try:
        data=discover(nas=nas);router=data["router"];nas.discovered_data=data;nas.routeros_version=router.get("version",nas.routeros_version);nas.last_connection_at=timezone.now();nas.last_sync_at=timezone.now();nas.lifecycle_status="ONLINE";nas.last_error_code="";nas.last_error_message="";nas.save();sync_freeradius(nas);audit(nas,user,"SYNC","SUCCESS",after=data);return data
    except NasServiceError as exc:
        nas.lifecycle_status="ERROR";nas.last_error_code=exc.code;nas.last_error_message=exc.message;nas.save(update_fields=["lifecycle_status","last_error_code","last_error_message"]);audit(nas,user,"SYNC","FAILURE",error=exc);raise


def radius_preview(nas):
    if not settings.RADIUS_SERVER_IP: raise NasServiceError("RADIUS_SERVER_IP_REQUIRED","RADIUS server IP is not configured")
    def run():
        with _client(nas,stored=True) as client:return client.preview_radius_changes(settings.RADIUS_SERVER_IP,decrypt_secret(nas.radius_secret_encrypted),nas.selected_radius_services,nas.radius_accounting_port,nas.coa_port)
    return _safe_router_call(run)


def configure_radius(nas,user,confirmed):
    if confirmed is not True:raise NasServiceError("CONFIRMATION_REQUIRED","Explicit confirmation is required")
    preview=radius_preview(nas)
    def run():
        with _client(nas,stored=True) as client:return client.configure_radius(settings.RADIUS_SERVER_IP,decrypt_secret(nas.radius_secret_encrypted),nas.selected_radius_services,nas.radius_accounting_port,nas.coa_port)
    try:
        result=_safe_router_call(run);sync_freeradius(nas);audit(nas,user,"CONFIGURE_RADIUS","SUCCESS",before=preview.get("existing") or {},after=result);return result
    except NasServiceError as exc:
        audit(nas,user,"CONFIGURE_RADIUS","FAILURE",before=preview.get("existing") or {},error=exc);raise


def health_check(nas):
    try:
        discovered=discover(nas=nas);resources=discovered["router"]
        health={"reachable":True,"checked_at":timezone.now().isoformat(),"uptime":resources.get("uptime"),"cpu_usage":resources.get("cpu_load"),"free_memory":resources.get("free_memory"),"interfaces_up":sum(1 for item in discovered["interfaces"] if item.get("running")=="true"),"active_ppp_sessions":len(discover_active(nas)),"radius_configured":any(item.get("address")==settings.RADIUS_SERVER_IP for item in discovered["radius"]),"last_error":None}
        nas.cached_health=health;nas.last_connection_at=timezone.now();nas.lifecycle_status="ONLINE";nas.save(update_fields=["cached_health","last_connection_at","lifecycle_status"]);return health
    except NasServiceError as exc:
        health={"reachable":False,"checked_at":timezone.now().isoformat(),"last_error":{"code":exc.code,"message":exc.message}};nas.cached_health=health;nas.lifecycle_status="OFFLINE";nas.last_error_code=exc.code;nas.last_error_message=exc.message;nas.save(update_fields=["cached_health","lifecycle_status","last_error_code","last_error_message"]);return health


def discover_active(nas):
    def run():
        with _client(nas,stored=True) as client:return client.get_active_sessions()
    return _safe_router_call(run)


def disconnect_router_session(nas,user,session_id):
    session=ActiveSession.objects.filter(Q(nas=nas)|Q(nas_ip_address=nas.radius_source_ip or nas.nas_ip_address),session_id=session_id).first()
    if not session:raise NasServiceError("SESSION_NOT_FOUND","Active session not found",404)
    def run():
        with _client(nas,stored=True) as client:return client.disconnect_session(session_id)
    _safe_router_call(run);session.status=ActiveSession.Status.DISCONNECTING;session.save(update_fields=["status"]);audit(nas,user,"DISCONNECT_SESSION","SUCCESS",{"session_id":session_id});return {"success":True,"session_id":session_id}


def safe_delete(nas,user):
    referenced=nas.subscribers.exists() or nas.active_sessions.exists() or nas.accounting_records.exists() or ActiveSession.objects.filter(nas_ip_address=nas.radius_source_ip or nas.nas_ip_address).exists() or AccountingRecord.objects.filter(nas_ip_address=nas.radius_source_ip or nas.nas_ip_address).exists()
    if referenced:
        nas.enabled=False;nas.lifecycle_status="DISABLED";nas.deleted_at=timezone.now();nas.save(update_fields=["enabled","lifecycle_status","deleted_at"]);audit(nas,user,"DISABLE","SUCCESS");return "disabled"
    audit(nas,user,"DELETE","SUCCESS");nas.delete();return "deleted"
