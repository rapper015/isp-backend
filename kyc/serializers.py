from rest_framework import serializers

from customers.models import Customer
from customers.serializers import CustomerSerializer

from .models import KycDocument


class KycDocumentSerializer(serializers.ModelSerializer):
    """Flat representation (bare customer id) - used for create/update responses."""

    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.filter(deleted_at__isnull=True)
    )
    document_number = serializers.CharField(
        max_length=128, required=False, allow_blank=True
    )

    class Meta:
        model = KycDocument
        fields = (
            "id",
            "customer",
            "document_type",
            "document_number",
            "file",
            "expiry_date",
            "status",
            "rejection_reason",
            "notes",
            "verified_by",
            "verified_at",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "status",
            "rejection_reason",
            "verified_by",
            "verified_at",
            "created_by",
        )


class KycDocumentDetailSerializer(KycDocumentSerializer):
    """Nested representation (populated customer) - used for list/detail responses."""

    customer = CustomerSerializer(read_only=True)
