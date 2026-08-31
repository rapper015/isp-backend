from django.db import models

from accounts.models import AdminUser


class Lead(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class ConnectionType(models.TextChoices):
        STU = "stu", "STU"
        MTU = "mtu", "MTU"

    class LeadSource(models.TextChoices):
        WEBSITE = "website", "Website"
        REFERRAL = "referral", "Referral"
        WALK_IN = "walk_in", "Walk-in"
        COLD_CALL = "cold_call", "Cold Call"
        SOCIAL_MEDIA = "social_media", "Social Media"
        FIELD_SURVEY = "field_survey", "Field Survey"
        OTHER = "other", "Other"

    class LeadType(models.TextChoices):
        HOME = "home", "Home"
        BUSINESS = "business", "Business"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        CONFIRM_FSB_PENDING = "confirm_fsb_pending", "Confirm FSB Pending"
        CAF_PENDING = "caf_pending", "CAF Pending"
        NC_PENDING = "nc_pending", "NC Pending"
        ACTIVE_CLOSED = "active_closed", "Active & Closed"
        NOT_INTERESTED = "not_interested", "Not Interested"
        OTHER_CLOSED = "other_closed", "Other Closed"

    franchise = models.ForeignKey(
        "resellers.Franchise",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="leads",
    )
    branch = models.ForeignKey(
        "resellers.Branch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="leads",
    )
    area = models.CharField(max_length=128, blank=True)
    colony = models.CharField(max_length=128, blank=True)
    connection_type = models.CharField(
        max_length=8, choices=ConnectionType.choices, blank=True
    )
    customer_name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=32)
    alt_mobile = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    lead_source = models.CharField(max_length=32, choices=LeadSource.choices, blank=True)
    lead_type = models.CharField(
        max_length=16, choices=LeadType.choices, default=LeadType.HOME
    )
    gender = models.CharField(max_length=8, choices=Gender.choices, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    is_callback = models.BooleanField(default=False)
    callback_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
    )
    created_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_leads",
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.customer_name} - {self.mobile}"
