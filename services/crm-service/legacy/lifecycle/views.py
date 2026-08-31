from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys
from customers.models import Customer

from .analytics import compute_lifecycle_analytics
from .models import CustomerLifecycleEvent
from .risk import compute_risk_profile
from .serializers import CustomerLifecycleEventSerializer
from .transitions import apply_transition


def _get_customer(customer_id):
    customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
    if customer is None:
        raise AppError("Customer not found", 404)
    return customer


def _admin_id(request):
    admin = request.user or {}
    admin_id = admin.get("userId")
    return int(admin_id) if admin_id else None


class CustomerLifecycleTransitionView(AdminAPIView):
    def post(self, request, customer_id):
        customer = _get_customer(customer_id)
        data = normalize_keys(request.data)
        to_status = data.get("to_status")
        if not to_status:
            raise AppError("Validation failed", 400, {"missingFields": ["toStatus"]})

        apply_transition(customer, to_status, _admin_id(request), data.get("reason", ""))

        event = customer.lifecycle_events.first()
        return Response(
            CustomerLifecycleEventSerializer(event).data, status=HTTP_201_CREATED
        )


class CustomerLifecycleEventListView(AdminAPIView):
    def get(self, request, customer_id):
        _get_customer(customer_id)
        events = CustomerLifecycleEvent.objects.filter(customer_id=customer_id)
        return Response(CustomerLifecycleEventSerializer(events, many=True).data)


class CustomerRiskProfileView(AdminAPIView):
    def get(self, request, customer_id):
        customer = _get_customer(customer_id)
        return Response(compute_risk_profile(customer))


class LifecycleAnalyticsView(AdminAPIView):
    def get(self, request):
        return Response(compute_lifecycle_analytics())
