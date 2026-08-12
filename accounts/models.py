from django.db import models


class AdminUser(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        BILLING_ADMIN = "billing_admin", "Billing Admin"
        NOC_ADMIN = "noc_admin", "NOC Admin"
        SUPPORT_ADMIN = "support_admin", "Support Admin"

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True, db_index=True)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(
        max_length=32, choices=Role.choices, default=Role.SUPER_ADMIN
    )
    is_active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    franchises = models.ManyToManyField("customers.Franchise", blank=True, related_name="admin_users")

    def __str__(self) -> str:
        return f"{self.email} ({self.role})"

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)
