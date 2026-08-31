from rest_framework import serializers

from .models import Branch, Franchise


class FranchiseSerializer(serializers.ModelSerializer):
    franchise_code = serializers.CharField(min_length=1, max_length=64)
    name = serializers.CharField(min_length=2, max_length=255)

    class Meta:
        model = Franchise
        fields = (
            "id",
            "franchise_code",
            "name",
            "contact_person",
            "phone",
            "email",
            "address",
            "city",
            "state",
            "status",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_by",)


class BranchSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(min_length=1, max_length=64)
    name = serializers.CharField(min_length=2, max_length=255)
    franchise_code = serializers.CharField(source="franchise.franchise_code", read_only=True)

    class Meta:
        model = Branch
        fields = (
            "id",
            "branch_code",
            "franchise",
            "franchise_code",
            "name",
            "contact_person",
            "phone",
            "email",
            "address",
            "city",
            "state",
            "status",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_by",)
