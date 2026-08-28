from rest_framework.response import Response
from rest_framework.views import APIView

from aaa.exceptions import AppError

from . import services
from .authentication import AdminBearerAuthentication
from .permissions import IsAuthenticatedAdmin


class AdminAPIView(APIView):
    """Base for /api/v1/* endpoints gated by admin JWT auth (any role)."""

    authentication_classes = [AdminBearerAuthentication]
    permission_classes = [IsAuthenticatedAdmin]


class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = request.data.get("email", "")
        password = request.data.get("password", "")
        if not email or not password:
            raise AppError("Validation failed", 400, {"missingFields": ["email", "password"]})
        return Response(services.login(email, password))
