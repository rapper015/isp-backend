from django import forms
from django.contrib import admin

from .models import Subscriber, SubscriberImportBatch, SubscriberImportRow
from .services import hash_password


class SubscriberAdminForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Enter a new plaintext password to set/replace the subscriber's "
        "PPPoE/RADIUS password. Leave blank to keep the current one.",
    )

    class Meta:
        model = Subscriber
        exclude = ("password_hash",)


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    form = SubscriberAdminForm
    list_display = (
        "subscriber_code",
        "username",
        "customer",
        "plan",
        "service_type",
        "status",
        "last_online_at",
    )
    list_filter = ("status", "service_type", "plan")
    search_fields = ("subscriber_code", "username", "static_ip_address", "mac_address")
    autocomplete_fields = ("customer", "plan")

    def save_model(self, request, obj, form, change):
        password = form.cleaned_data.get("password")
        if password:
            obj.password_hash = hash_password(password)
        elif not change:
            raise forms.ValidationError("A password is required when creating a subscriber.")
        super().save_model(request, obj, form, change)


@admin.register(SubscriberImportBatch)
class SubscriberImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "franchise", "original_filename", "status", "total_rows", "created_rows", "failed_rows", "created_at")
    list_filter = ("status", "franchise")
    readonly_fields = tuple(field.name for field in SubscriberImportBatch._meta.fields)


@admin.register(SubscriberImportRow)
class SubscriberImportRowAdmin(admin.ModelAdmin):
    list_display = ("import_batch", "source_row_number", "external_id", "username", "action", "target_subscriber")
    list_filter = ("action",)
    readonly_fields = tuple(field.name for field in SubscriberImportRow._meta.fields)
