from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys

from .models import Branch, Franchise
from .scoping import (
    assert_can_write_branch,
    assert_can_write_franchise,
    get_scope,
    scope_branch_queryset,
    scope_franchise_queryset,
)
from .serializers import BranchSerializer, FranchiseSerializer


def _admin_id(request):
    admin = request.user or {}
    admin_id = admin.get("userId")
    return int(admin_id) if admin_id else None


class FranchiseListCreateView(AdminAPIView):
    def get(self, request):
        scope = get_scope(request)
        franchises = scope_franchise_queryset(
            Franchise.objects.filter(deleted_at__isnull=True), scope
        ).order_by("-created_at")

        status_param = request.query_params.get("status")
        if status_param:
            franchises = franchises.filter(status=status_param)

        return Response(FranchiseSerializer(franchises, many=True).data)

    def post(self, request):
        scope = get_scope(request)
        assert_can_write_franchise(scope)

        serializer = FranchiseSerializer(data=normalize_keys(request.data))
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by_id=_admin_id(request))
        return Response(serializer.data, status=HTTP_201_CREATED)


class FranchiseDetailView(AdminAPIView):
    def _get(self, request, franchise_id):
        scope = get_scope(request)
        franchise = scope_franchise_queryset(
            Franchise.objects.filter(id=franchise_id, deleted_at__isnull=True), scope
        ).first()
        if franchise is None:
            raise AppError("Franchise not found", 404)
        return franchise

    def get(self, request, franchise_id):
        franchise = self._get(request, franchise_id)
        return Response(FranchiseSerializer(franchise).data)

    def patch(self, request, franchise_id):
        franchise = self._get(request, franchise_id)
        assert_can_write_franchise(get_scope(request))
        serializer = FranchiseSerializer(
            franchise, data=normalize_keys(request.data), partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, franchise_id):
        franchise = self._get(request, franchise_id)
        assert_can_write_franchise(get_scope(request))
        franchise.deleted_at = timezone.now()
        franchise.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)


class BranchListCreateView(AdminAPIView):
    def get(self, request):
        scope = get_scope(request)
        branches = scope_branch_queryset(
            Branch.objects.filter(deleted_at__isnull=True), scope
        ).order_by("-created_at")

        franchise_id = request.query_params.get("franchiseId") or request.query_params.get(
            "franchise_id"
        )
        if franchise_id:
            branches = branches.filter(franchise_id=franchise_id)

        status_param = request.query_params.get("status")
        if status_param:
            branches = branches.filter(status=status_param)

        return Response(BranchSerializer(branches, many=True).data)

    def post(self, request):
        scope = get_scope(request)
        data = normalize_keys(request.data)

        franchise_id = data.get("franchise")
        if not franchise_id:
            raise AppError("Validation failed", 400, {"missingFields": ["franchise"]})
        assert_can_write_branch(scope, int(franchise_id))

        serializer = BranchSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by_id=_admin_id(request))
        return Response(serializer.data, status=HTTP_201_CREATED)


class BranchDetailView(AdminAPIView):
    def _get(self, request, branch_id):
        scope = get_scope(request)
        branch = scope_branch_queryset(
            Branch.objects.filter(id=branch_id, deleted_at__isnull=True), scope
        ).first()
        if branch is None:
            raise AppError("Branch not found", 404)
        return branch

    def get(self, request, branch_id):
        branch = self._get(request, branch_id)
        return Response(BranchSerializer(branch).data)

    def patch(self, request, branch_id):
        branch = self._get(request, branch_id)
        assert_can_write_branch(get_scope(request), branch.franchise_id)
        serializer = BranchSerializer(branch, data=normalize_keys(request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, branch_id):
        branch = self._get(request, branch_id)
        assert_can_write_branch(get_scope(request), branch.franchise_id)
        branch.deleted_at = timezone.now()
        branch.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
