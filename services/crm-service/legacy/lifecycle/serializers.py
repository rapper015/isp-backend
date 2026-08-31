from rest_framework import serializers

from .models import CustomerLifecycleEvent


class CustomerLifecycleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerLifecycleEvent
        fields = (
            "id",
            "customer",
            "from_status",
            "to_status",
            "reason",
            "performed_by",
            "created_at",
        )
        read_only_fields = fields
