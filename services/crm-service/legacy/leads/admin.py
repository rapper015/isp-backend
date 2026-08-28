from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "mobile",
        "franchise",
        "branch",
        "status",
        "priority",
        "assigned_to",
    )
    list_filter = ("status", "priority", "lead_type", "is_callback", "franchise", "branch")
    search_fields = ("customer_name", "mobile", "alt_mobile", "email")
