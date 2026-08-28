from django.contrib import admin

from .models import AccountingRecord, ActiveSession, AuthLog


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(ActiveSession)
class ActiveSessionAdmin(ReadOnlyAdmin):
    list_display = (
        "username",
        "session_id",
        "nas_ip_address",
        "framed_ip_address",
        "status",
        "started_at",
        "last_interim_at",
    )
    list_filter = ("status",)
    search_fields = ("username", "session_id", "nas_ip_address", "framed_ip_address")


@admin.register(AccountingRecord)
class AccountingRecordAdmin(ReadOnlyAdmin):
    list_display = (
        "username",
        "session_id",
        "event_type",
        "nas_ip_address",
        "input_octets",
        "output_octets",
        "event_at",
    )
    list_filter = ("event_type",)
    search_fields = ("username", "session_id", "nas_ip_address")
    date_hierarchy = "event_at"


@admin.register(AuthLog)
class AuthLogAdmin(ReadOnlyAdmin):
    list_display = ("username", "action", "result", "message", "logged_at")
    list_filter = ("action", "result")
    search_fields = ("username", "message")
    date_hierarchy = "logged_at"
