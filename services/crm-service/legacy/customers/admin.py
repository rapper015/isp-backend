from django.contrib import admin

from .models import Area, Branch, Customer, Franchise


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "full_name", "phone", "city", "status")
    list_filter = ("status", "city", "zone")
    search_fields = ("customer_code", "full_name", "phone", "email")


admin.site.register(Franchise)
admin.site.register(Branch)
admin.site.register(Area)
