import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from customers.models import Customer
from plans.models import Plan
from subscribers.models import Subscriber


class BillingSettings(models.Model):
    settings_key = models.CharField(max_length=32, unique=True, default="default")
    invoice_prefix = models.CharField(max_length=16, default="INV")
    default_due_days = models.PositiveIntegerField(default=5)
    grace_period_days = models.PositiveIntegerField(default=3)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    suspension_enabled = models.BooleanField(default=True)
    auto_suspend_on_overdue = models.BooleanField(default=False)
    default_billing_day = models.PositiveIntegerField(default=1)
    currency = models.CharField(max_length=8, default="INR")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"BillingSettings({self.settings_key})"


class BillingAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        CLOSED = "closed", "Closed"

    account_code = models.CharField(max_length=64, unique=True, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="billing_accounts")
    subscriber = models.OneToOneField(
        Subscriber, on_delete=models.CASCADE, related_name="billing_account"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="billing_accounts")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    outstanding_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    billing_day = models.PositiveIntegerField(default=1)
    due_days = models.PositiveIntegerField(default=5)
    grace_period_days = models.PositiveIntegerField(default=3)
    suspension_enabled = models.BooleanField(default=True)
    auto_generate_invoices = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    last_invoice_at = models.DateTimeField(null=True, blank=True)
    next_invoice_date = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.account_code


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        VOID = "void", "Void"
        CANCELLED = "cancelled", "Cancelled"

    invoice_number = models.CharField(max_length=64, unique=True, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    subscriber = models.ForeignKey(Subscriber, on_delete=models.PROTECT, related_name="invoices")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="invoices")
    billing_period_start = models.DateTimeField()
    billing_period_end = models.DateTimeField()
    due_date = models.DateTimeField(db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_due = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ISSUED, db_index=True
    )
    notes = models.CharField(max_length=255, blank=True)
    line_items = models.JSONField(default=list, encoder=DjangoJSONEncoder)
    source_system = models.CharField(max_length=64, blank=True, db_index=True)
    source_invoice_number = models.CharField(max_length=128, blank=True, db_index=True)
    source_order_number = models.CharField(max_length=128, blank=True)
    import_metadata = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.invoice_number

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("subscriber", "source_system", "source_invoice_number"),
                condition=~models.Q(source_invoice_number=""),
                name="uq_invoice_subscriber_source_number",
            )
        ]


class LedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        PAYMENT = "payment", "Payment"
        ADJUSTMENT = "adjustment", "Adjustment"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="ledger_entries")
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=16, choices=EntryType.choices, db_index=True)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_impact = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255)
    posted_at = models.DateTimeField(db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.entry_type} - {self.description}"


class SequenceCounter(models.Model):
    """Backs atomic invoice/payment number generation (see billing/sequences.py)."""

    prefix = models.CharField(max_length=16)
    period = models.CharField(max_length=8)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("prefix", "period")

    def __str__(self) -> str:
        return f"{self.prefix}-{self.period}: {self.last_value}"


def invoice_import_upload_to(instance, filename):
    return f"invoice_imports/{instance.id}/{filename}"


class InvoiceImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        VALIDATING = "VALIDATING", "Validating"
        VALIDATED = "VALIDATED", "Validated"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        PARTIAL = "PARTIAL", "Partial"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    franchise = models.ForeignKey("customers.Franchise", on_delete=models.PROTECT, related_name="invoice_imports")
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=invoice_import_upload_to)
    file_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED, db_index=True)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    created_rows = models.PositiveIntegerField(default=0)
    updated_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    validation_summary = models.JSONField(default=dict, blank=True)
    column_mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey("accounts.AdminUser", on_delete=models.PROTECT, related_name="invoice_imports")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class InvoiceImportRow(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        SKIP = "SKIP", "Skip"
        ERROR = "ERROR", "Error"

    import_batch = models.ForeignKey(InvoiceImportBatch, on_delete=models.CASCADE, related_name="rows")
    source_row_number = models.PositiveIntegerField()
    source_invoice_number = models.CharField(max_length=128, blank=True)
    username = models.CharField(max_length=128, blank=True)
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict)
    action = models.CharField(max_length=8, choices=Action.choices, db_index=True)
    validation_errors = models.JSONField(default=list, blank=True)
    processing_error = models.TextField(blank=True)
    target_invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="import_rows")
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("import_batch", "source_row_number"), name="uq_invoice_import_batch_row")]
        ordering = ("source_row_number",)
