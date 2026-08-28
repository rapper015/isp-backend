import uuid

from django.db import models


class NasDevice(models.Model):
    class ApiProtocol(models.TextChoices):
        API = "API", "RouterOS API"
        API_SSL = "API_SSL", "RouterOS API SSL"

    class LifecycleStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        TESTING = "TESTING", "Testing"
        ONLINE = "ONLINE", "Online"
        OFFLINE = "OFFLINE", "Offline"
        ERROR = "ERROR", "Error"
        DISABLED = "DISABLED", "Disabled"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    nas_ip_address = models.CharField(max_length=64, db_index=True)
    nas_identifier = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=128, blank=True)
    vendor = models.CharField(max_length=128, blank=True)
    model = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UNKNOWN, db_index=True
    )
    service_types = models.JSONField(default=list, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    franchise = models.ForeignKey("customers.Franchise", null=True, blank=True, on_delete=models.PROTECT, related_name="nas_devices")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    short_name = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    nas_type = models.CharField(max_length=64, default="mikrotik")
    radius_source_ip = models.CharField(max_length=64, blank=True)
    api_port = models.PositiveIntegerField(default=8729)
    api_protocol = models.CharField(max_length=16, choices=ApiProtocol.choices, default=ApiProtocol.API_SSL)
    api_username = models.CharField(max_length=128, blank=True)
    encrypted_api_password = models.TextField(blank=True)
    radius_secret_encrypted = models.TextField(blank=True)
    radius_auth_port = models.PositiveIntegerField(default=1812)
    radius_accounting_port = models.PositiveIntegerField(default=1813)
    coa_port = models.PositiveIntegerField(default=3799)
    routeros_version = models.CharField(max_length=128, blank=True)
    architecture = models.CharField(max_length=128, blank=True)
    board_name = models.CharField(max_length=128, blank=True)
    serial_number = models.CharField(max_length=128, blank=True)
    system_identity = models.CharField(max_length=255, blank=True)
    lifecycle_status = models.CharField(max_length=16, choices=LifecycleStatus.choices, default=LifecycleStatus.DRAFT, db_index=True)
    last_connection_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
    connection_timeout = models.PositiveSmallIntegerField(default=5)
    verify_tls = models.BooleanField(default=True)
    certificate_fingerprint = models.CharField(max_length=128, blank=True)
    ca_certificate = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    selected_radius_services = models.JSONField(default=list, blank=True)
    discovered_data = models.JSONField(default=dict, blank=True)
    cached_health = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey("accounts.AdminUser", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_nas_devices")
    updated_by = models.ForeignKey("accounts.AdminUser", null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_nas_devices")

    def __str__(self) -> str:
        return self.name or self.nas_ip_address

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("franchise", "nas_ip_address"), condition=models.Q(deleted_at__isnull=True, franchise__isnull=False), name="uq_nas_ip_tenant"),
            models.UniqueConstraint(fields=("nas_ip_address",), condition=models.Q(deleted_at__isnull=True, franchise__isnull=True), name="uq_nas_ip_legacy"),
            models.UniqueConstraint(fields=("radius_source_ip",), condition=~models.Q(radius_source_ip="") & models.Q(deleted_at__isnull=True), name="uq_nas_radius_source_global"),
        ]


class NetworkLocation(models.Model):
    class Kind(models.TextChoices):
        NODE = "node", "Node"
        POP = "pop", "POP"
        SWITCH = "switch", "Switch"

    franchise = models.ForeignKey("customers.Franchise", on_delete=models.CASCADE, related_name="network_locations")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("franchise", "kind", "normalized_name"), name="uq_network_location_scope")]

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()}: {self.name}"


class FreeRadiusClient(models.Model):
    nas = models.OneToOneField(NasDevice, on_delete=models.CASCADE, related_name="freeradius_client")
    franchise = models.ForeignKey("customers.Franchise", on_delete=models.PROTECT, related_name="freeradius_clients")
    source_ip = models.CharField(max_length=64, unique=True)
    short_name = models.CharField(max_length=64)
    nas_type = models.CharField(max_length=64, default="mikrotik")
    secret_encrypted = models.TextField()
    enabled = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class NasAuditLog(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"

    nas = models.ForeignKey(NasDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    franchise = models.ForeignKey("customers.Franchise", on_delete=models.PROTECT, related_name="nas_audit_logs")
    user = models.ForeignKey("accounts.AdminUser", null=True, blank=True, on_delete=models.SET_NULL, related_name="nas_audit_logs")
    action = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    safe_request_data = models.JSONField(default=dict, blank=True)
    previous_configuration = models.JSONField(default=dict, blank=True)
    new_configuration = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
