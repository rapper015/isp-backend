from django.contrib import admin

from aaa.admin import ReadOnlyAdmin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("payment_reference", "customer", "subscriber", "amount", "method", "received_at")
    list_filter = ("method",)
    search_fields = ("payment_reference",)
    date_hierarchy = "received_at"
