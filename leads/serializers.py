from rest_framework import serializers

from .models import Lead


class LeadSerializer(serializers.ModelSerializer):
    franchise = serializers.CharField(min_length=1, max_length=128)
    branch = serializers.CharField(min_length=1, max_length=128)
    customer_name = serializers.CharField(min_length=2, max_length=255)
    mobile = serializers.CharField(min_length=6, max_length=32)
    notes = serializers.CharField(max_length=100, required=False, allow_blank=True)

    class Meta:
        model = Lead
        fields = (
            "id",
            "franchise",
            "branch",
            "area",
            "colony",
            "connection_type",
            "customer_name",
            "mobile",
            "alt_mobile",
            "email",
            "priority",
            "lead_source",
            "lead_type",
            "gender",
            "latitude",
            "longitude",
            "address",
            "notes",
            "status",
            "is_callback",
            "callback_at",
            "assigned_to",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_by",)
