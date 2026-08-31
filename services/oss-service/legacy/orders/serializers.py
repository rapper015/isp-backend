from rest_framework import serializers

from .models import Order, OrderEvent


class OrderSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "order_type",
            "customer",
            "subscriber",
            "plan",
            "franchise",
            "branch",
            "trigger_invoice",
            "subscriber_username",
            "service_type",
            "installation_address",
            "mac_address",
            "status",
            "requested_by",
            "assigned_to",
            "notes",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "order_number",
            "order_type",
            "customer",
            "subscriber",
            "trigger_invoice",
            "subscriber_username",
            "service_type",
            "status",
            "requested_by",
        )


class OrderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderEvent
        fields = (
            "id",
            "order",
            "event_type",
            "from_status",
            "to_status",
            "step_name",
            "detail",
            "performed_by",
            "created_at",
        )
        read_only_fields = fields
