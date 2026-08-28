from django.db import models

from accounts.models import AdminUser
from customers.models import Customer


def kyc_document_upload_path(instance, filename):
    return f"kyc/{instance.customer_id}/{filename}"


class KycDocument(models.Model):
    class DocumentType(models.TextChoices):
        NATIONAL_ID = "national_id", "National ID"
        PASSPORT = "passport", "Passport"
        DRIVING_LICENSE = "driving_license", "Driving License"
        UTILITY_BILL = "utility_bill", "Utility Bill"
        ADDRESS_PROOF = "address_proof", "Address Proof"
        BUSINESS_REGISTRATION = "business_registration", "Business Registration"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="kyc_documents"
    )
    document_type = models.CharField(max_length=32, choices=DocumentType.choices)
    document_number = models.CharField(max_length=128, blank=True)
    file = models.FileField(upload_to=kyc_document_upload_path)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    rejection_reason = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    verified_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_kyc_documents",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        AdminUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_kyc_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return f"{self.customer_id} - {self.document_type} ({self.status})"
