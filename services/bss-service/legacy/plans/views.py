from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys

from .models import Plan
from .serializers import PlanSerializer


class PlanListCreateView(AdminAPIView):
    def get(self, request):
        plans = Plan.objects.filter(deleted_at__isnull=True).order_by("monthly_fee", "name")
        return Response(PlanSerializer(plans, many=True).data)

    def post(self, request):
        serializer = PlanSerializer(data=normalize_keys(request.data))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_201_CREATED)


class PlanDetailView(AdminAPIView):
    def get(self, request, plan_id):
        plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
        if plan is None:
            raise AppError("Plan not found", 404)
        return Response(PlanSerializer(plan).data)

    def patch(self, request, plan_id):
        plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
        if plan is None:
            raise AppError("Plan not found", 404)
        serializer = PlanSerializer(plan, data=normalize_keys(request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, plan_id):
        plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
        if plan is None:
            raise AppError("Plan not found", 404)
        plan.deleted_at = timezone.now()
        plan.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
