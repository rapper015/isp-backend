import jwt as pyjwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .jwt import decode_admin_token


class AdminPrincipal(dict):
    """JWT claims with the authentication interface expected by DRF."""

    @property
    def is_authenticated(self):
        return True

    @property
    def pk(self):
        return self.get("userId")


class AdminBearerAuthentication(BaseAuthentication):
    """Stateless admin JWT auth, mirroring authenticateAdmin.ts.

    Trusts the token payload as-is (userId/email/role) without re-hitting the
    database on every request, matching the Node reference.
    """

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationFailed("Missing bearer token")

        token = header[len("Bearer ") :]
        try:
            payload = decode_admin_token(token)
        except pyjwt.PyJWTError:
            raise AuthenticationFailed("Invalid or expired token")

        return AdminPrincipal(payload), token

    def authenticate_header(self, request):
        # Presence of this (non-empty) return value keeps DRF from downgrading
        # AuthenticationFailed/NotAuthenticated from 401 to 403.
        return "Bearer"
