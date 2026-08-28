from rest_framework import serializers

from .models import NasDevice


class NasDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NasDevice
        fields = (
            "id",
            "nas_ip_address",
            "nas_identifier",
            "name",
            "vendor",
            "model",
            "status",
            "service_types",
            "last_seen_at",
            "notes",
            "created_at",
            "updated_at",
        )
