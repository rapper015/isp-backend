from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView

from . import services
from .serializers import NasDeviceSerializer


class NasDeviceListView(AdminAPIView):
    def get(self, request):
        entries = services.list_nas_devices()
        data = []
        for entry in entries:
            device_data = NasDeviceSerializer(entry["device"]).data
            device_data["online_session_count"] = entry["online_session_count"]
            device_data["active_subscribers"] = entry["active_subscribers"]
            data.append(device_data)
        return Response(data)


class NasDeviceDetailView(AdminAPIView):
    def delete(self, request, device_id):
        services.delete_nas_device(device_id)
        return Response(status=HTTP_204_NO_CONTENT)
