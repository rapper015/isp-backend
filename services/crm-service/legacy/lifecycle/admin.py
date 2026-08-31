from django.contrib import admin

from .models import CustomerLifecycleEvent


@admin.register(CustomerLifecycleEvent)
class CustomerLifecycleEventAdmin(admin.ModelAdmin):
    list_display = ("customer", "from_status", "to_status", "performed_by", "created_at")
    list_filter = ("from_status", "to_status")
    search_fields = ("customer__full_name", "customer__customer_code", "reason")
