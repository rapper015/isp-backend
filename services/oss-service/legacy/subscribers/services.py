from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aaa.exceptions import AppError
from common.passwords import hash_password, verify_password
from customers.models import Customer
from plans.models import Plan

from .models import Subscriber

__all__ = [
    "hash_password",
    "verify_password",
    "list_subscribers",
    "get_subscriber",
    "create_subscriber",
    "update_subscriber",
    "delete_subscriber",
]


def _get(data: dict, *keys: str, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def list_subscribers():
    return Subscriber.objects.select_related("customer", "plan").filter(
        deleted_at__isnull=True
    ).order_by("-created_at")


def get_subscriber(subscriber_id) -> Subscriber:
    subscriber = (
        Subscriber.objects.select_related("customer", "plan")
        .filter(id=subscriber_id, deleted_at__isnull=True)
        .first()
    )
    if subscriber is None:
        raise AppError("Subscriber not found", 404)
    return subscriber


def delete_subscriber(subscriber_id) -> None:
    subscriber = get_subscriber(subscriber_id)
    subscriber.deleted_at = timezone.now()
    subscriber.save(update_fields=["deleted_at"])


def create_subscriber(data: dict) -> Subscriber:
    subscriber_code = _get(data, "subscriberCode", "subscriber_code")
    customer_id = _get(data, "customerId", "customer_id")
    plan_id = _get(data, "planId", "plan_id")
    username = _get(data, "username")
    password = _get(data, "password")
    installation_address = _get(data, "installationAddress", "installation_address")

    missing = [
        name
        for name, value in (
            ("subscriberCode", subscriber_code),
            ("customerId", customer_id),
            ("planId", plan_id),
            ("username", username),
            ("password", password),
            ("installationAddress", installation_address),
        )
        if not value
    ]
    if missing:
        raise AppError("Validation failed", 400, {"missingFields": missing})

    customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
    if customer is None:
        raise AppError("Customer not found", 400)

    plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
    if plan is None:
        raise AppError("Plan not found", 400)

    expires_at_raw = _get(data, "expiresAt", "expires_at")

    return Subscriber.objects.create(
        subscriber_code=subscriber_code,
        customer=customer,
        plan=plan,
        username=username,
        password_hash=hash_password(password),
        service_type=_get(data, "serviceType", "service_type", default=Subscriber.ServiceType.PPPOE),
        status=_get(data, "status", default=Subscriber.Status.ACTIVE),
        expires_at=parse_datetime(expires_at_raw) if expires_at_raw else None,
        installation_address=installation_address,
        current_balance=_get(data, "currentBalance", "current_balance", default=0),
        suspension_reason=_get(data, "suspensionReason", "suspension_reason", default=""),
        static_ip_address=_get(data, "staticIpAddress", "static_ip_address", default=""),
        mac_address=_get(data, "macAddress", "mac_address", default=""),
    )


def update_subscriber(subscriber_id, data: dict) -> Subscriber:
    subscriber = get_subscriber(subscriber_id)

    direct_fields = {
        "subscriber_code": ("subscriberCode", "subscriber_code"),
        "username": ("username",),
        "service_type": ("serviceType", "service_type"),
        "status": ("status",),
        "installation_address": ("installationAddress", "installation_address"),
        "current_balance": ("currentBalance", "current_balance"),
        "suspension_reason": ("suspensionReason", "suspension_reason"),
        "static_ip_address": ("staticIpAddress", "static_ip_address"),
        "mac_address": ("macAddress", "mac_address"),
    }
    for field, keys in direct_fields.items():
        value = _get(data, *keys)
        if value is not None:
            setattr(subscriber, field, value)

    # No referential existence check on customer/plan change, matching the
    # Node reference (only validated on create, not on update).
    customer_id = _get(data, "customerId", "customer_id")
    if customer_id is not None:
        subscriber.customer_id = customer_id

    plan_id = _get(data, "planId", "plan_id")
    if plan_id is not None:
        subscriber.plan_id = plan_id

    password = _get(data, "password")
    if password:
        subscriber.password_hash = hash_password(password)

    expires_at_raw = _get(data, "expiresAt", "expires_at")
    if expires_at_raw is not None:
        subscriber.expires_at = parse_datetime(expires_at_raw)

    subscriber.save()
    return subscriber
