from django.contrib import admin

from .models import Order, OrderEvent


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "order_type", "customer", "subscriber", "plan", "status")
    list_filter = ("status", "order_type")
    search_fields = ("order_number", "subscriber_username")


@admin.register(OrderEvent)
class OrderEventAdmin(admin.ModelAdmin):
    list_display = ("order", "event_type", "step_name", "from_status", "to_status", "created_at")
    list_filter = ("event_type",)
    search_fields = ("order__order_number", "step_name")
