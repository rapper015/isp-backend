import logging
import re

from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("aaa")

_SENSITIVE_KEY_PATTERN = re.compile(r"password|token|secret|authorization|api[-_]?key", re.I)


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 500, details=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def redact_sensitive_fields(value):
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]

    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if _SENSITIVE_KEY_PATTERN.search(str(key)) else redact_sensitive_fields(v))
            for key, v in value.items()
        }

    return value


def _log_request_failure(request, status_code):
    body = getattr(request, "data", None)
    logger.error(
        "Request failed",
        extra={
            "statusCode": status_code,
            "method": request.method,
            "path": request.path,
            "body": redact_sensitive_fields(body) if body is not None else None,
        },
    )


def internal_aaa_exception_handler(exc, context):
    request = context.get("request")

    if isinstance(exc, AppError):
        if request is not None:
            _log_request_failure(request, exc.status_code)
        logger.exception(exc)
        return Response(
            {"message": exc.message, "details": exc.details},
            status=exc.status_code,
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        if request is not None:
            _log_request_failure(request, response.status_code)
        message = None
        if isinstance(response.data, dict):
            message = response.data.get("detail")
        if isinstance(exc, drf_exceptions.APIException) and message:
            response.data = {"message": str(message), "details": None}
        else:
            response.data = {"message": "Request failed", "details": response.data}
        return response

    if request is not None:
        _log_request_failure(request, 500)
    logger.exception(exc)
    return Response({"message": "Internal server error"}, status=500)
