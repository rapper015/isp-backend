from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys
from lifecycle.transitions import apply_transition

from .models import Customer
from .serializers import CustomerSerializer


def _admin_id(request):
    admin = request.user or {}
    admin_id = admin.get("userId")
    return int(admin_id) if admin_id else None


class CustomerListCreateView(AdminAPIView):
    def get(self, request):
        customers = Customer.objects.filter(deleted_at__isnull=True).order_by("-created_at")
        return Response(CustomerSerializer(customers, many=True).data)

    def post(self, request):
        serializer = CustomerSerializer(data=normalize_keys(request.data))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_201_CREATED)


class CustomerDetailView(AdminAPIView):
    def get(self, request, customer_id):
        customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
        if customer is None:
            raise AppError("Customer not found", 404)
        return Response(CustomerSerializer(customer).data)

    def patch(self, request, customer_id):
        customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
        if customer is None:
            raise AppError("Customer not found", 404)

        data = normalize_keys(request.data)
        # Status changes are governed by the lifecycle state machine (allowed
        # transitions, verified-KYC-before-activation) and logged as an
        # auditable CustomerLifecycleEvent - see lifecycle/transitions.py.
        # This is the same enforcement POST .../lifecycle/transition uses, so
        # a status change is governed the same way regardless of which
        # endpoint an admin/frontend happens to use.
        new_status = data.pop("status", None)
        reason = data.pop("reason", "")
        if new_status and new_status != customer.status:
            customer = apply_transition(customer, new_status, _admin_id(request), reason)

        if data:
            serializer = CustomerSerializer(customer, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        else:
            serializer = CustomerSerializer(customer)
        return Response(serializer.data)

    def delete(self, request, customer_id):
        customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
        if customer is None:
            raise AppError("Customer not found", 404)
        customer.deleted_at = timezone.now()
        customer.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
