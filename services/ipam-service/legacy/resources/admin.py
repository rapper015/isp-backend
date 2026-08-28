from django.contrib import admin

from .models import IPAddress, IPPool, VlanPool


@admin.register(IPPool)
class IPPoolAdmin(admin.ModelAdmin):
    list_display = ("pool_code", "name", "network_cidr", "franchise", "branch", "status")
    list_filter = ("status", "franchise", "branch")
    search_fields = ("pool_code", "name", "network_cidr")


@admin.register(IPAddress)
class IPAddressAdmin(admin.ModelAdmin):
    list_display = ("address", "pool", "status", "allocated_subscriber", "allocated_at")
    list_filter = ("status", "pool")
    search_fields = ("address",)


@admin.register(VlanPool)
class VlanPoolAdmin(admin.ModelAdmin):
    list_display = ("vlan_id", "name", "franchise", "branch", "status")
    list_filter = ("status", "franchise", "branch")
    search_fields = ("name",)
