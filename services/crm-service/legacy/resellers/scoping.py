from aaa.exceptions import AppError
from accounts.models import AdminUser

from .models import Branch

_UNRESTRICTED_ROLES = {
    AdminUser.Role.SUPER_ADMIN,
    AdminUser.Role.BILLING_ADMIN,
    AdminUser.Role.NOC_ADMIN,
    AdminUser.Role.SUPPORT_ADMIN,
}


def get_scope(request) -> dict:
    """Pulls role/franchiseId/branchId off the JWT payload (request.user)."""
    payload = request.user or {}
    franchise_id = payload.get("franchiseId")
    branch_id = payload.get("branchId")
    return {
        "role": payload.get("role"),
        "franchise_id": int(franchise_id) if franchise_id else None,
        "branch_id": int(branch_id) if branch_id else None,
    }


def _branch_franchise_id(scope: dict):
    """A branch admin's franchise, falling back to a lookup if the token
    only carries branchId (e.g. franchise wasn't set on the admin row)."""
    if scope["franchise_id"]:
        return scope["franchise_id"]
    if scope["branch_id"]:
        branch = Branch.objects.filter(id=scope["branch_id"]).first()
        return branch.franchise_id if branch else None
    return None


def scope_franchise_queryset(queryset, scope: dict):
    if scope["role"] in _UNRESTRICTED_ROLES:
        return queryset
    if scope["role"] == AdminUser.Role.FRANCHISE_ADMIN:
        return queryset.filter(id=scope["franchise_id"])
    if scope["role"] == AdminUser.Role.BRANCH_ADMIN:
        return queryset.filter(id=_branch_franchise_id(scope))
    return queryset.none()


def scope_branch_queryset(queryset, scope: dict):
    if scope["role"] in _UNRESTRICTED_ROLES:
        return queryset
    if scope["role"] == AdminUser.Role.FRANCHISE_ADMIN:
        return queryset.filter(franchise_id=scope["franchise_id"])
    if scope["role"] == AdminUser.Role.BRANCH_ADMIN:
        return queryset.filter(id=scope["branch_id"])
    return queryset.none()


def scope_lead_queryset(queryset, scope: dict):
    if scope["role"] in _UNRESTRICTED_ROLES:
        return queryset
    if scope["role"] == AdminUser.Role.FRANCHISE_ADMIN:
        return queryset.filter(franchise_id=scope["franchise_id"])
    if scope["role"] == AdminUser.Role.BRANCH_ADMIN:
        return queryset.filter(branch_id=scope["branch_id"])
    return queryset.none()


def assert_can_write_franchise(scope: dict):
    if scope["role"] != AdminUser.Role.SUPER_ADMIN:
        raise AppError("Only a super admin can manage franchises", 403)


def assert_can_write_branch(scope: dict, franchise_id):
    if scope["role"] == AdminUser.Role.SUPER_ADMIN:
        return
    if scope["role"] == AdminUser.Role.FRANCHISE_ADMIN and scope["franchise_id"] == franchise_id:
        return
    raise AppError("You cannot manage branches outside your franchise", 403)


def resolve_lead_source(scope: dict, requested_franchise_id, requested_branch_id):
    """Fills in / validates a Lead's franchise & branch based on who's creating it.

    Franchise/branch admins are pinned to their own org - any client-supplied
    value outside that scope is rejected rather than silently overridden.
    """
    if scope["role"] == AdminUser.Role.BRANCH_ADMIN:
        if requested_branch_id and int(requested_branch_id) != scope["branch_id"]:
            raise AppError("Validation failed", 400, {"invalidField": "branch"})
        return _branch_franchise_id(scope), scope["branch_id"]

    if scope["role"] == AdminUser.Role.FRANCHISE_ADMIN:
        if requested_franchise_id and int(requested_franchise_id) != scope["franchise_id"]:
            raise AppError("Validation failed", 400, {"invalidField": "franchise"})
        if requested_branch_id:
            branch = Branch.objects.filter(id=requested_branch_id).first()
            if branch is None or branch.franchise_id != scope["franchise_id"]:
                raise AppError("Validation failed", 400, {"invalidField": "branch"})
        return scope["franchise_id"], requested_branch_id

    return requested_franchise_id, requested_branch_id
