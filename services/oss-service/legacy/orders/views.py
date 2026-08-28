from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from common.casing import normalize_keys

from . import services
from .models import Order
from .orchestration import run_provisioning_saga
from .serializers import OrderEventSerializer, OrderSerializer
from .transitions import apply_order_transition


def _admin_id(request):
    admin = request.user or {}
    admin_id = admin.get("userId")
    return int(admin_id) if admin_id else None


class OrderListCreateView(AdminAPIView):
    def get(self, request):
        orders = services.list_orders()

        status_param = request.query_params.get("status")
        if status_param:
            orders = orders.filter(status=status_param)

        order_type = request.query_params.get("orderType") or request.query_params.get(
            "order_type"
        )
        if order_type:
            orders = orders.filter(order_type=order_type)

        customer_id = request.query_params.get("customerId") or request.query_params.get(
            "customer_id"
        )
        if customer_id:
            orders = orders.filter(customer_id=customer_id)

        franchise_id = request.query_params.get("franchiseId") or request.query_params.get(
            "franchise_id"
        )
        if franchise_id:
            orders = orders.filter(franchise_id=franchise_id)

        branch_id = request.query_params.get("branchId") or request.query_params.get("branch_id")
        if branch_id:
            orders = orders.filter(branch_id=branch_id)

        return Response(OrderSerializer(orders, many=True).data)

    def post(self, request):
        order = services.create_order(normalize_keys(request.data), _admin_id(request))
        return Response(OrderSerializer(order).data, status=HTTP_201_CREATED)


class OrderDetailView(AdminAPIView):
    def get(self, request, order_id):
        return Response(OrderSerializer(services.get_order(order_id)).data)

    def patch(self, request, order_id):
        order = services.update_order(order_id, normalize_keys(request.data))
        return Response(OrderSerializer(order).data)

    def delete(self, request, order_id):
        services.delete_order(order_id)
        return Response(status=HTTP_204_NO_CONTENT)


class OrderEventListView(AdminAPIView):
    def get(self, request, order_id):
        order = services.get_order(order_id)
        return Response(OrderEventSerializer(order.events.all(), many=True).data)


class OrderActivateView(AdminAPIView):
    def post(self, request, order_id):
        order = services.get_order(order_id)
        run_provisioning_saga(order, _admin_id(request))
        return Response(OrderSerializer(order).data)


class OrderCancelView(AdminAPIView):
    def post(self, request, order_id):
        order = services.get_order(order_id)
        data = normalize_keys(request.data)
        apply_order_transition(
            order, Order.Status.CANCELLED, _admin_id(request), data.get("reason", "")
        )
        return Response(OrderSerializer(order).data)
