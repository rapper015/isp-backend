from django.db import models

from accounts.models import AdminUser
from billing.models import Invoice
from customers.models import Customer
from plans.models import Plan
from subscribers.models import Subscriber


class Order(models.Model):
    class OrderType(models.TextChoices):
        NEW_SERVICE = "new_service", "New Service"
        UPGRADE = "upgrade", "Upgrade"
        DOWNGRADE = "downgrade", "Downgrade"
        DISCONNECT = "disconnect", "Disconnect"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    order_type = models.CharField(max_length=16, choices=OrderType.choices)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, null=True, blank=True, related_name="orders"
    )
    franchise = models.ForeignKey(
        "resellers.Franchise",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    branch = models.ForeignKey(
        "resellers.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    trigger_invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_orders",
    )
    # New-service provisioning details - only required for order_type=new_service,
    # where there's no existing Subscriber row yet to read them from.
    subscriber_username = models.CharField(max_length=128, blank=True)
    subscriber_password_hash = models.CharField(max_length=255, blank=True)
    service_type = models.CharField(
        max_length=16, choices=Subscriber.ServiceType.choices, blank=True
    )
    installation_address = models.CharField(max_length=255, blank=True)
    mac_address = models.CharField(max_length=32, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    requested_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_orders",
    )
    assigned_to = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_orders",
    )
    notes = models.CharField(max_length=255, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.order_number} ({self.order_type})"


class OrderEvent(models.Model):
    class EventType(models.TextChoices):
        STATUS_CHANGED = "status_changed", "Status Changed"
        STEP_STARTED = "step_started", "Step Started"
        STEP_COMPLETED = "step_completed", "Step Completed"
        STEP_FAILED = "step_failed", "Step Failed"
        STEP_COMPENSATED = "step_compensated", "Step Compensated"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=16, choices=EventType.choices, db_index=True)
    from_status = models.CharField(
        max_length=16, choices=Order.Status.choices, blank=True
    )
    to_status = models.CharField(max_length=16, choices=Order.Status.choices, blank=True)
    step_name = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_events",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.order_id} - {self.event_type}"
