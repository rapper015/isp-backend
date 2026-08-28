from rest_framework import serializers

from .models import Customer
from .sequences import next_customer_code


class CustomerSerializer(serializers.ModelSerializer):
    customer_code = serializers.CharField(read_only=True)
    full_name = serializers.CharField(min_length=2, max_length=255)
    phone = serializers.CharField(min_length=6, max_length=32)
    address = serializers.CharField(min_length=5, max_length=255)
    city = serializers.CharField(min_length=2, max_length=128)

    class Meta:
        model = Customer
        fields = (
            "id",
            "customer_code",
            "full_name",
            "phone",
            "email",
            "address",
            "city",
            "zone",
            "status",
            "national_id",
            "notes",
            "created_at",
            "updated_at",
        )

    def create(self, validated_data):
        validated_data["customer_code"] = next_customer_code()
        return super().create(validated_data)
