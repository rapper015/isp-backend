from abc import ABC, abstractmethod


class RouterError(Exception):
    code="ROUTER_ERROR"

class RouterTimeout(RouterError): code="CONNECTION_TIMEOUT"
class RouterConnectionRefused(RouterError): code="CONNECTION_REFUSED"
class RouterAuthenticationError(RouterError): code="AUTHENTICATION_FAILED"
class RouterPermissionError(RouterError): code="INSUFFICIENT_PERMISSION"
class RouterTLSError(RouterError): code="TLS_FAILED"
class RouterResponseError(RouterError): code="MALFORMED_RESPONSE"


class RouterClient(ABC):
    def __enter__(self): self.connect(); return self
    def __exit__(self,*_): self.close()
    @abstractmethod
    def connect(self): ...
    @abstractmethod
    def close(self): ...
    @abstractmethod
    def command(self,path,attributes=None,queries=None): ...

    def test_connection(self): return self.get_identity()
    def get_identity(self): return self.command("/system/identity/print")
    def get_system_resources(self): return self.command("/system/resource/print")
    def get_interfaces(self): return self.command("/interface/print")
    def get_ip_addresses(self): return self.command("/ip/address/print")
    def get_radius_servers(self): return self.command("/radius/print")
    def get_ppp_aaa(self): return self.command("/ppp/aaa/print")
    def get_pppoe_servers(self): return self.command("/interface/pppoe-server/server/print")
    def get_hotspot_servers(self): return self.command("/ip/hotspot/print")
    def get_hotspot_profiles(self): return self.command("/ip/hotspot/profile/print")
    def get_ip_pools(self): return self.command("/ip/pool/print")
    def get_active_sessions(self): return self.command("/ppp/active/print")

    def preview_radius_changes(self,server,secret,services,accounting_port,coa_port=3799,timeout="300ms"):
        existing=self.get_radius_servers()
        match=next((item for item in existing if item.get("address")==server),None)
        router_services=list(dict.fromkeys("ppp" if item=="pppoe" else item for item in services))
        desired={"address":server,"service":",".join(router_services),"authentication-port":"1812","accounting-port":str(accounting_port),"timeout":timeout}
        return {"existing":match,"desired":desired,"radius_incoming":{"accept":"yes","port":str(coa_port)},"action":"UPDATE" if match else "CREATE","conflicts":[]}

    def configure_radius(self,server,secret,services,accounting_port,coa_port=3799,interim_interval="5m"):
        preview=self.preview_radius_changes(server,secret,services,accounting_port,coa_port)
        attrs={**preview["desired"],"secret":secret,"disabled":"no"}
        if preview["existing"]:
            self.command("/radius/set",{".id":preview["existing"][".id"],**attrs})
        else: self.command("/radius/add",attrs)
        if any(item in services for item in ("ppp","pppoe")):
            aaa=self.get_ppp_aaa()
            if aaa: self.command("/ppp/aaa/set",{".id":aaa[0].get(".id","*0"),"use-radius":"yes","accounting":"yes","interim-update":interim_interval})
        if "hotspot" in services:
            for profile in self.get_hotspot_profiles():
                self.command("/ip/hotspot/profile/set",{".id":profile[".id"],"use-radius":"yes","radius-accounting":"yes","radius-interim-update":interim_interval})
        self.command("/radius/incoming/set",{"accept":"yes","port":str(coa_port)})
        return {"before":preview["existing"],"after":self.get_radius_servers(),"verified":True}

    def disconnect_session(self,session_id): return self.command("/ppp/active/remove",{".id":session_id})
