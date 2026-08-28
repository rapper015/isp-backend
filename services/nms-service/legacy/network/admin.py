from django.contrib import admin

from .models import FreeRadiusClient, NasAuditLog, NasDevice, NetworkLocation


@admin.register(NasDevice)
class NasDeviceAdmin(admin.ModelAdmin):
    list_display = ("nas_ip_address", "name", "nas_identifier", "status", "last_seen_at")
    list_filter = ("status",)
    search_fields = ("nas_ip_address", "name", "nas_identifier")
    exclude = ("encrypted_api_password", "radius_secret_encrypted", "ca_certificate")


admin.site.register(NetworkLocation)


@admin.register(FreeRadiusClient)
class FreeRadiusClientAdmin(admin.ModelAdmin):
    list_display = ("nas", "source_ip", "short_name", "enabled", "verified_at")
    exclude = ("secret_encrypted",)


@admin.register(NasAuditLog)
class NasAuditLogAdmin(admin.ModelAdmin):
    list_display = ("nas", "franchise", "user", "action", "status", "created_at")
    readonly_fields = tuple(field.name for field in NasAuditLog._meta.fields)
