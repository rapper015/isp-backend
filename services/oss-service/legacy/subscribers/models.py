import uuid

from django.conf import settings
from django.db import models

from customers.models import Customer
from plans.models import Plan


class Subscriber(models.Model):
    class ServiceType(models.TextChoices):
        PPPOE = "pppoe", "PPPoE"
        HOTSPOT = "hotspot", "Hotspot"
        STATIC_IP = "static_ip", "Static IP"
        DHCP = "dhcp", "DHCP"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        TERMINATED = "terminated", "Terminated"

    subscriber_code = models.CharField(max_length=64, unique=True, db_index=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="subscribers"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscribers")
    username = models.CharField(max_length=128, db_index=True)
    password_hash = models.CharField(max_length=255)
    service_type = models.CharField(
        max_length=16, choices=ServiceType.choices, default=ServiceType.PPPOE
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    installation_address = models.CharField(max_length=255)
    current_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    suspension_reason = models.CharField(max_length=255, blank=True)
    static_ip_address = models.CharField(max_length=64, blank=True)
    mac_address = models.CharField(max_length=32, blank=True)
    last_online_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    franchise = models.ForeignKey("customers.Franchise", null=True, blank=True, on_delete=models.PROTECT, related_name="subscribers")
    external_id = models.CharField(max_length=128, null=True, blank=True)
    source_system = models.CharField(max_length=64, blank=True)
    outage_enabled = models.BooleanField(null=True, blank=True)
    account_type = models.CharField(max_length=32, blank=True)
    connection_type = models.CharField(max_length=64, blank=True)
    auto_renew = models.BooleanField(default=False)
    allowed_macs = models.JSONField(default=list, blank=True)
    nas = models.ForeignKey("network.NasDevice", null=True, blank=True, on_delete=models.SET_NULL, related_name="subscribers")
    nas_port_id = models.CharField(max_length=255, blank=True)
    node = models.ForeignKey("network.NetworkLocation", null=True, blank=True, on_delete=models.SET_NULL, related_name="node_subscribers")
    pop = models.ForeignKey("network.NetworkLocation", null=True, blank=True, on_delete=models.SET_NULL, related_name="pop_subscribers")
    switch = models.ForeignKey("network.NetworkLocation", null=True, blank=True, on_delete=models.SET_NULL, related_name="switch_subscribers")
    last_logoff_at = models.DateTimeField(null=True, blank=True)
    last_renewal_at = models.DateTimeField(null=True, blank=True)
    fup_limit = models.CharField(max_length=128, blank=True)
    source_package_name = models.CharField(max_length=255, blank=True)
    source_sub_package = models.CharField(max_length=255, blank=True)
    package_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    custom_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    special_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    additional_charges = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_payment_source = models.CharField(max_length=255, blank=True)
    pop_technical_executive = models.CharField(max_length=255, blank=True)
    pop_collection_executive = models.CharField(max_length=255, blank=True)
    import_metadata = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.subscriber_code} - {self.username}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("franchise", "username"), condition=models.Q(deleted_at__isnull=True, franchise__isnull=False), name="uq_subscriber_username_tenant"),
            models.UniqueConstraint(fields=("username",), condition=models.Q(deleted_at__isnull=True, franchise__isnull=True), name="uq_subscriber_username_legacy"),
            models.UniqueConstraint(fields=("franchise", "source_system", "external_id"), condition=models.Q(external_id__isnull=False), name="uq_subscriber_source_external_tenant"),
            models.UniqueConstraint(fields=("franchise", "mac_address"), condition=~models.Q(mac_address="") & models.Q(deleted_at__isnull=True, franchise__isnull=False), name="uq_subscriber_mac_tenant"),
            models.UniqueConstraint(fields=("franchise", "static_ip_address"), condition=~models.Q(static_ip_address="") & models.Q(deleted_at__isnull=True, franchise__isnull=False), name="uq_subscriber_ip_tenant"),
        ]


def subscriber_import_upload_to(instance, filename):
    return f"subscriber_imports/{instance.id}/{filename}"


class SubscriberImportBatch(models.Model):
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
    franchise = models.ForeignKey("customers.Franchise", on_delete=models.PROTECT, related_name="subscriber_imports")
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=subscriber_import_upload_to)
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
    created_by = models.ForeignKey("accounts.AdminUser", on_delete=models.PROTECT, related_name="subscriber_imports")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class SubscriberImportRow(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        SKIP = "SKIP", "Skip"
        ERROR = "ERROR", "Error"

    import_batch = models.ForeignKey(SubscriberImportBatch, on_delete=models.CASCADE, related_name="rows")
    source_row_number = models.PositiveIntegerField()
    external_id = models.CharField(max_length=128, blank=True)
    username = models.CharField(max_length=128, blank=True)
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict)
    action = models.CharField(max_length=8, choices=Action.choices, db_index=True)
    validation_errors = models.JSONField(default=list, blank=True)
    processing_error = models.TextField(blank=True)
    target_subscriber = models.ForeignKey(Subscriber, null=True, blank=True, on_delete=models.SET_NULL, related_name="import_rows")
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("import_batch", "source_row_number"), name="uq_import_batch_row")]
        ordering = ("source_row_number",)
