from django.contrib import admin

from .models import KycDocument


@admin.register(KycDocument)
class KycDocumentAdmin(admin.ModelAdmin):
    list_display = ("customer", "document_type", "status", "verified_by", "created_at")
    list_filter = ("status", "document_type")
    search_fields = ("customer__full_name", "customer__customer_code", "document_number")
