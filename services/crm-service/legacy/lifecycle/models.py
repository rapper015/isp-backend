from django.db import models

from accounts.models import AdminUser
from customers.models import Customer


class CustomerLifecycleEvent(models.Model):
    """Audit trail entry for a single Customer.status transition."""

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="lifecycle_events"
    )
    from_status = models.CharField(max_length=16, choices=Customer.Status.choices)
    to_status = models.CharField(max_length=16, choices=Customer.Status.choices, db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lifecycle_events",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.customer_id}: {self.from_status} -> {self.to_status}"
