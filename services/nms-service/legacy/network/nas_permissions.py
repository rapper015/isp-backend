from rest_framework.permissions import BasePermission


class CanManageNas(BasePermission):
    message="Only super administrators or assigned NOC administrators may manage NAS devices."
    def has_permission(self,request,view): return bool(request.user) and request.user.get("role") in ("super_admin","noc_admin")
