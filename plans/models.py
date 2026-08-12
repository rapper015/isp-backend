from django.db import models


class Plan(models.Model):
    class PlanType(models.TextChoices):
        PPPOE = "pppoe", "PPPoE"
        HOTSPOT = "hotspot", "Hotspot"
        HYBRID = "hybrid", "Hybrid"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    plan_code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2)
    speed_profile_name = models.CharField(max_length=128)
    download_rate_kbps = models.PositiveIntegerField()
    upload_rate_kbps = models.PositiveIntegerField()
    ip_pool = models.CharField(max_length=128, blank=True)
    vlan = models.CharField(max_length=32, blank=True)
    plan_type = models.CharField(
        max_length=16, choices=PlanType.choices, default=PlanType.PPPOE
    )
    billing_cycle_days = models.PositiveIntegerField(default=30)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    franchise = models.ForeignKey("customers.Franchise", null=True, blank=True, on_delete=models.PROTECT, related_name="plans")
    source_name = models.CharField(max_length=255, blank=True)
    source_sub_package = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return f"{self.plan_code} - {self.name}"
