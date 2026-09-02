"""Small, consistent CORS configuration helpers for HTTP services."""
from __future__ import annotations

import os


# These cover common local frontend dev-server ports when deployment
# configuration is not supplied. Production deployments should set the variable
# to their exact frontend origins.
DEFAULT_LOCAL_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


def cors_allowed_origins() -> list[str]:
    """Return normalized origins from ``CORS_ALLOWED_ORIGINS``.

    Origins are comma-separated and must include scheme and port where one is
    used, for example ``https://app.example.com,http://localhost:5173``.
    """
    value = os.getenv("CORS_ALLOWED_ORIGINS")
    if value is None:
        return list(DEFAULT_LOCAL_ORIGINS)
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
