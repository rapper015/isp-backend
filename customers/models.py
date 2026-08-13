from django.db import models


class Franchise(models.Model):
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("normalized_name"), name="uq_franchise_normalized_name_ci"
            )
        ]

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Branch(models.Model):
    franchise = models.ForeignKey(Franchise, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("franchise", "normalized_name"), name="uq_branch_franchise_name")]

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Area(models.Model):
    franchise = models.ForeignKey(Franchise, on_delete=models.CASCADE, related_name="areas")
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("franchise", "normalized_name"), name="uq_area_franchise_name")]

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Customer(models.Model):
    class Status(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        TERMINATED = "terminated", "Terminated"

    customer_code = models.CharField(max_length=64, unique=True, db_index=True)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    zone = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    national_id = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    franchise = models.ForeignKey(Franchise, null=True, blank=True, on_delete=models.PROTECT, related_name="customers")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    external_id = models.CharField(max_length=128, null=True, blank=True)
    source_system = models.CharField(max_length=64, blank=True)
    caf_number = models.CharField(max_length=128, blank=True)
    father_or_company_name = models.CharField(max_length=255, blank=True)
    alternate_phone = models.CharField(max_length=32, blank=True)
    gstin = models.CharField(max_length=32, blank=True)
    colony = models.CharField(max_length=255, blank=True)
    building = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=128, blank=True)
    door_number = models.CharField(max_length=128, blank=True)
    billing_address = models.TextField(blank=True)
    installation_address = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    caf_form_available = models.BooleanField(null=True, blank=True)
    address_proof_available = models.BooleanField(null=True, blank=True)
    identity_proof_available = models.BooleanField(null=True, blank=True)
    customer_picture_available = models.BooleanField(null=True, blank=True)
    source_added_at = models.DateTimeField(null=True, blank=True)
    commitment_date = models.DateTimeField(null=True, blank=True)
    source_created_by = models.CharField(max_length=255, blank=True)
    import_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return f"{self.customer_code} - {self.full_name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("franchise", "source_system", "external_id"), condition=models.Q(external_id__isnull=False), name="uq_customer_source_external_tenant")
        ]


class CustomerCodeSequence(models.Model):
    """Backs atomic customer_code generation (see customers/sequences.py)."""

    last_value = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"CustomerCodeSequence({self.last_value})"
