from rest_framework import serializers

from .models import BillingAccount, BillingSettings, Invoice, LedgerEntry


class BillingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingSettings
        fields = (
            "id",
            "settings_key",
            "invoice_prefix",
            "default_due_days",
            "grace_period_days",
            "tax_percent",
            "suspension_enabled",
            "auto_suspend_on_overdue",
            "default_billing_day",
            "currency",
            "created_at",
            "updated_at",
        )


class BillingAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingAccount
        fields = (
            "id",
            "account_code",
            "customer",
            "subscriber",
            "plan",
            "status",
            "outstanding_balance",
            "billing_day",
            "due_days",
            "grace_period_days",
            "suspension_enabled",
            "auto_generate_invoices",
            "notes",
            "last_invoice_at",
            "next_invoice_date",
            "created_at",
            "updated_at",
        )


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = (
            "id",
            "invoice_number",
            "customer",
            "subscriber",
            "plan",
            "billing_period_start",
            "billing_period_end",
            "due_date",
            "subtotal",
            "tax_amount",
            "amount",
            "balance_due",
            "status",
            "notes",
            "line_items",
            "source_system",
            "source_invoice_number",
            "source_order_number",
            "import_metadata",
            "created_at",
            "updated_at",
        )


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = (
            "id",
            "customer",
            "subscriber",
            "invoice",
            "payment",
            "entry_type",
            "debit",
            "credit",
            "balance_impact",
            "description",
            "posted_at",
            "created_at",
            "updated_at",
        )
