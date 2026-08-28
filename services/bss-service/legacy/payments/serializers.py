from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "payment_reference",
            "invoice",
            "customer",
            "subscriber",
            "amount",
            "method",
            "received_at",
            "notes",
            "created_at",
            "updated_at",
        )
