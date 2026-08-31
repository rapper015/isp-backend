from rest_framework.permissions import BasePermission


class CanImportSubscribers(BasePermission):
    message = "Only super administrators may import subscribers."

    def has_permission(self, request, view):
        return bool(request.user) and request.user.get("role") == "super_admin"
