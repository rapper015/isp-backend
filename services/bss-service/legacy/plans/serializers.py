from rest_framework import serializers

from .models import Plan


class PlanSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(min_length=2, max_length=64)
    name = serializers.CharField(min_length=2, max_length=255)
    speed_profile_name = serializers.CharField(min_length=2, max_length=128)
    download_rate_kbps = serializers.IntegerField(min_value=1)
    upload_rate_kbps = serializers.IntegerField(min_value=1)
    monthly_fee = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    billing_cycle_days = serializers.IntegerField(min_value=1, required=False)

    class Meta:
        model = Plan
        fields = (
            "id",
            "plan_code",
            "name",
            "description",
            "monthly_fee",
            "speed_profile_name",
            "download_rate_kbps",
            "upload_rate_kbps",
            "ip_pool",
            "vlan",
            "plan_type",
            "billing_cycle_days",
            "status",
            "created_at",
            "updated_at",
        )
