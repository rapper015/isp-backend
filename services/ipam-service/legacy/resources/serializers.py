from rest_framework import serializers

from .models import IPAddress, IPPool, VlanPool


class IPPoolSerializer(serializers.ModelSerializer):
    pool_code = serializers.CharField(min_length=1, max_length=64)
    name = serializers.CharField(min_length=2, max_length=255)

    class Meta:
        model = IPPool
        fields = (
            "id",
            "pool_code",
            "name",
            "network_cidr",
            "gateway",
            "dns_primary",
            "dns_secondary",
            "franchise",
            "branch",
            "status",
            "created_at",
            "updated_at",
        )


class IPAddressSerializer(serializers.ModelSerializer):
    pool_code = serializers.CharField(source="pool.pool_code", read_only=True)

    class Meta:
        model = IPAddress
        fields = (
            "id",
            "pool",
            "pool_code",
            "address",
            "status",
            "allocated_order",
            "allocated_subscriber",
            "allocated_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "status",
            "allocated_order",
            "allocated_subscriber",
            "allocated_at",
        )


class VlanPoolSerializer(serializers.ModelSerializer):
    name = serializers.CharField(min_length=2, max_length=255)

    class Meta:
        model = VlanPool
        fields = (
            "id",
            "vlan_id",
            "name",
            "franchise",
            "branch",
            "status",
            "created_at",
            "updated_at",
        )
