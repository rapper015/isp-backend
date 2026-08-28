from django.db import models

from customers.models import Customer
from subscribers.models import Subscriber


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        CARD = "card", "Card"
        MOBILE_MONEY = "mobile_money", "Mobile Money"

    payment_reference = models.CharField(max_length=64, unique=True, db_index=True)
    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="payments"
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=16, choices=Method.choices)
    received_at = models.DateTimeField(db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return self.payment_reference
