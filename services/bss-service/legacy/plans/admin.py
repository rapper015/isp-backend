from django.contrib import admin

from .models import Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "plan_code",
        "name",
        "monthly_fee",
        "download_rate_kbps",
        "upload_rate_kbps",
        "plan_type",
        "status",
    )
    list_filter = ("plan_type", "status")
    search_fields = ("plan_code", "name", "speed_profile_name")
