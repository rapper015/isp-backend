from django.db.models import Q
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED,HTTP_204_NO_CONTENT
from rest_framework.throttling import ScopedRateThrottle

from aaa.exceptions import AppError
from aaa.models import ActiveSession
from aaa.serializers import ActiveSessionSerializer
from accounts.models import AdminUser
from accounts.views import AdminAPIView

from . import nas_services
from .nas_permissions import CanManageNas
from .nas_serializers import ConnectionSerializer,NasAuditSerializer,NasPatchSerializer,NasSerializer,NasWriteSerializer


class NasAPIView(AdminAPIView):
    permission_classes=[CanManageNas]
    def user(self,request):
        user=AdminUser.objects.filter(id=request.user.get("userId"),is_active=True).first()
        if not user:raise AppError("Admin user not found",401)
        return user
    def nas(self,request,nas_id):
        try:return nas_services.get_nas(nas_id,request.user)
        except nas_services.NasServiceError as exc:raise AppError(exc.message,exc.status,{"code":exc.code}) from exc
    def execute(self,callback):
        try:return callback()
        except nas_services.NasServiceError as exc:raise AppError(exc.message,exc.status,{"code":exc.code}) from exc


class TestConnectionView(NasAPIView):
    throttle_classes=[ScopedRateThrottle];throttle_scope="nas_connection_test"
    def post(self,request):
        serializer=ConnectionSerializer(data=request.data);serializer.is_valid(raise_exception=True)
        return Response(self.execute(lambda:nas_services.test_connection(serializer.validated_data)))


class DiscoverView(NasAPIView):
    throttle_classes=[ScopedRateThrottle];throttle_scope="nas_connection_test"
    def post(self,request):
        serializer=ConnectionSerializer(data=request.data);serializer.is_valid(raise_exception=True)
        return Response(self.execute(lambda:nas_services.discover(config=serializer.validated_data)))


class NasListCreateView(NasAPIView):
    def get(self,request): return Response(NasSerializer(nas_services.scoped_nas(request.user).order_by("name"),many=True).data)
    def post(self,request):
        serializer=NasWriteSerializer(data=request.data);serializer.is_valid(raise_exception=True)
        nas=self.execute(lambda:nas_services.create_nas(serializer.validated_data,self.user(request)))
        return Response(NasSerializer(nas).data,status=HTTP_201_CREATED)


class NasDetailView(NasAPIView):
    def get(self,request,nas_id):return Response(NasSerializer(self.nas(request,nas_id)).data)
    def patch(self,request,nas_id):
        serializer=NasPatchSerializer(data=request.data);serializer.is_valid(raise_exception=True)
        return Response(NasSerializer(self.execute(lambda:nas_services.update_nas(self.nas(request,nas_id),serializer.validated_data,self.user(request)))).data)
    def delete(self,request,nas_id):
        result=self.execute(lambda:nas_services.safe_delete(self.nas(request,nas_id),self.user(request)))
        return Response({"result":result},status=HTTP_204_NO_CONTENT if result=="deleted" else 200)


class StoredTestView(NasAPIView):
    throttle_classes=[ScopedRateThrottle];throttle_scope="nas_connection_test"
    def post(self,request,nas_id):return Response(self.execute(lambda:nas_services.test_connection_stored(self.nas(request,nas_id))))


class SyncView(NasAPIView):
    def post(self,request,nas_id):return Response(self.execute(lambda:nas_services.sync_nas(self.nas(request,nas_id),self.user(request))))


class RadiusPreviewView(NasAPIView):
    def get(self,request,nas_id):return Response(self.execute(lambda:nas_services.radius_preview(self.nas(request,nas_id))))


class ConfigureRadiusView(NasAPIView):
    def post(self,request,nas_id):return Response(self.execute(lambda:nas_services.configure_radius(self.nas(request,nas_id),self.user(request),request.data.get("confirm") is True)))


class CachedHealthView(NasAPIView):
    def get(self,request,nas_id):return Response(self.nas(request,nas_id).cached_health)


class DiscoveryPartView(NasAPIView):
    key=None
    def get(self,request,nas_id):return Response(self.nas(request,nas_id).discovered_data.get(self.key,[]))


class InterfacesView(DiscoveryPartView):key="interfaces"
class IpAddressesView(DiscoveryPartView):key="ip_addresses"
class RadiusView(DiscoveryPartView):key="radius"
class PppoeView(DiscoveryPartView):key="pppoe_servers"
class HotspotView(DiscoveryPartView):key="hotspot_servers"
class PoolsView(DiscoveryPartView):key="ip_pools"


class ActiveSessionsView(NasAPIView):
    def get(self,request,nas_id):
        nas=self.nas(request,nas_id);qs=ActiveSession.objects.filter(Q(nas=nas)|Q(nas_ip_address=nas.radius_source_ip or nas.nas_ip_address),deleted_at__isnull=True)
        return Response(ActiveSessionSerializer(qs,many=True).data)


class DisconnectView(NasAPIView):
    def post(self,request,nas_id):
        session_id=request.data.get("session_id")
        if not session_id:raise AppError("session_id is required",400)
        return Response(self.execute(lambda:nas_services.disconnect_router_session(self.nas(request,nas_id),self.user(request),session_id)))


class AuditView(NasAPIView):
    def get(self,request,nas_id):return Response(NasAuditSerializer(self.nas(request,nas_id).audit_logs.order_by("-created_at")[:250],many=True).data)
