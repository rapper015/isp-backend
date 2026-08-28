from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys
from resellers.scoping import get_scope, resolve_lead_source, scope_lead_queryset

from .models import Lead
from .serializers import LeadSerializer

_TRUE_VALUES = {"1", "true", "yes"}


class LeadListCreateView(AdminAPIView):
    def get(self, request):
        leads = scope_lead_queryset(
            Lead.objects.filter(deleted_at__isnull=True), get_scope(request)
        ).order_by("-created_at")

        status_param = request.query_params.get("status")
        if status_param:
            leads = leads.filter(status=status_param)

        assigned_to = request.query_params.get("assignedTo") or request.query_params.get(
            "assigned_to"
        )
        if assigned_to:
            leads = leads.filter(assigned_to_id=assigned_to)

        is_callback = request.query_params.get("isCallback") or request.query_params.get(
            "is_callback"
        )
        if is_callback is not None:
            leads = leads.filter(is_callback=is_callback.lower() in _TRUE_VALUES)

        return Response(LeadSerializer(leads, many=True).data)

    def post(self, request):
        scope = get_scope(request)
        data = normalize_keys(request.data)
        franchise_id, branch_id = resolve_lead_source(
            scope, data.get("franchise"), data.get("branch")
        )
        data["franchise"] = franchise_id
        data["branch"] = branch_id

        serializer = LeadSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        admin = request.user or {}
        admin_id = admin.get("userId")
        serializer.save(created_by_id=int(admin_id) if admin_id else None)
        return Response(serializer.data, status=HTTP_201_CREATED)


class LeadDetailView(AdminAPIView):
    def _get(self, request, lead_id):
        lead = scope_lead_queryset(
            Lead.objects.filter(id=lead_id, deleted_at__isnull=True), get_scope(request)
        ).first()
        if lead is None:
            raise AppError("Lead not found", 404)
        return lead

    def get(self, request, lead_id):
        lead = self._get(request, lead_id)
        return Response(LeadSerializer(lead).data)

    def patch(self, request, lead_id):
        lead = self._get(request, lead_id)
        serializer = LeadSerializer(lead, data=normalize_keys(request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, lead_id):
        lead = self._get(request, lead_id)
        lead.deleted_at = timezone.now()
        lead.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
