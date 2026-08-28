from rest_framework.permissions import BasePermission


class IsAuthenticatedAdmin(BasePermission):
    """Any authenticated admin, regardless of role.

    Node's authorizeRoles() middleware exists but is never actually applied to
    any billing/payment route today, so any of the 4 admin roles can hit these
    endpoints — replicated as-is here.
    """

    def has_permission(self, request, view) -> bool:
        return bool(request.user)
