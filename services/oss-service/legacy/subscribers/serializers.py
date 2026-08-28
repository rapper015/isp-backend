from rest_framework import serializers

from customers.serializers import CustomerSerializer
from plans.serializers import PlanSerializer

from .models import Subscriber


class SubscriberSerializer(serializers.ModelSerializer):
    """Flat representation (bare customer/plan ids) - used for create/update responses."""

    class Meta:
        model = Subscriber
        fields = (
            "id",
            "subscriber_code",
            "customer",
            "plan",
            "username",
            "service_type",
            "status",
            "expires_at",
            "installation_address",
            "current_balance",
            "suspension_reason",
            "static_ip_address",
            "mac_address",
            "last_online_at",
            "created_at",
            "updated_at",
        )


class SubscriberDetailSerializer(SubscriberSerializer):
    """Nested representation (populated customer/plan) - used for list/detail responses."""

    customer = CustomerSerializer(read_only=True)
    plan = PlanSerializer(read_only=True)
