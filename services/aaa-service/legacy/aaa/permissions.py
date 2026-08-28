from django.conf import settings
from rest_framework.permissions import BasePermission


class HasInternalApiKey(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.headers.get("x-internal-api-key") == settings.INTERNAL_API_KEY
