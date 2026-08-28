from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AdminBearerAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.AdminBearerAuthentication"
    name = "AdminBearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Admin JWT returned by POST /api/v1/auth/login.",
        }
