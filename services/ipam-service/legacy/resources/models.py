from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class IPPool(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    pool_code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    network_cidr = models.CharField(max_length=64)
    gateway = models.GenericIPAddressField(null=True, blank=True)
    dns_primary = models.GenericIPAddressField(null=True, blank=True)
    dns_secondary = models.GenericIPAddressField(null=True, blank=True)
    franchise = models.ForeignKey(
        "resellers.Franchise",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ip_pools",
    )
    branch = models.ForeignKey(
        "resellers.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ip_pools",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.pool_code} - {self.name}"


class IPAddress(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        ALLOCATED = "allocated", "Allocated"

    pool = models.ForeignKey(IPPool, on_delete=models.PROTECT, related_name="addresses")
    address = models.GenericIPAddressField(unique=True, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.AVAILABLE, db_index=True
    )
    allocated_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocated_ip_addresses",
    )
    allocated_subscriber = models.ForeignKey(
        "subscribers.Subscriber",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocated_ip_addresses",
    )
    allocated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.address


class VlanPool(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    vlan_id = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        validators=[MinValueValidator(1), MaxValueValidator(4094)],
    )
    name = models.CharField(max_length=255)
    franchise = models.ForeignKey(
        "resellers.Franchise",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vlan_pools",
    )
    branch = models.ForeignKey(
        "resellers.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vlan_pools",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"VLAN {self.vlan_id} - {self.name}"
