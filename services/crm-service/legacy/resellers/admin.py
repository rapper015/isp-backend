from django.contrib import admin

from .models import Branch, Franchise


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = ("franchise_code", "name", "city", "state", "status")
    list_filter = ("status", "city", "state")
    search_fields = ("franchise_code", "name", "contact_person", "phone", "email")


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("branch_code", "name", "franchise", "city", "state", "status")
    list_filter = ("status", "franchise", "city", "state")
    search_fields = ("branch_code", "name", "contact_person", "phone", "email")
