from django.contrib import admin

from aaa.admin import ReadOnlyAdmin

from .models import BillingAccount, BillingSettings, Invoice, InvoiceImportBatch, InvoiceImportRow, LedgerEntry


@admin.register(BillingSettings)
class BillingSettingsAdmin(admin.ModelAdmin):
    list_display = ("settings_key", "invoice_prefix", "tax_percent", "currency")


@admin.register(BillingAccount)
class BillingAccountAdmin(ReadOnlyAdmin):
    list_display = (
        "account_code",
        "customer",
        "subscriber",
        "plan",
        "status",
        "outstanding_balance",
    )
    list_filter = ("status",)
    search_fields = ("account_code",)


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = (
        "invoice_number",
        "customer",
        "subscriber",
        "amount",
        "balance_due",
        "status",
        "due_date",
    )
    list_filter = ("status",)
    search_fields = ("invoice_number", "source_invoice_number", "source_order_number")
    date_hierarchy = "due_date"


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyAdmin):
    list_display = ("customer", "subscriber", "entry_type", "debit", "credit", "posted_at")
    list_filter = ("entry_type",)
    date_hierarchy = "posted_at"


admin.site.register(InvoiceImportBatch, ReadOnlyAdmin)
admin.site.register(InvoiceImportRow, ReadOnlyAdmin)
