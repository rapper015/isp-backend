"""BSS security: internal service key auth + management JWT with RBAC."""
import secrets
from os import getenv

import jwt
from fastapi import HTTPException, Request


ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "BSS_MANAGER": {
        "bss.invoice.view", "bss.invoice.manage", "bss.payment.view", "bss.payment.manage",
        "bss.payment.capture", "bss.refund.approve", "bss.manual_payment.approve",
        "bss.ledger.view", "bss.reconciliation.manage", "bss.dunning.manage",
        "bss.gateway.manage", "bss.webhook.view", "bss.report.view", "bss.audit.view",
    },
    "BSS_OPERATOR": {
        "bss.invoice.view", "bss.payment.view", "bss.payment.manage", "bss.manual_payment.submit",
        "bss.ledger.view", "bss.reconciliation.view", "bss.dunning.view", "bss.webhook.view",
        "bss.report.view", "bss.audit.view",
    },
    "FINANCE_MANAGER": {
        "bss.invoice.view", "bss.payment.view", "bss.refund.approve", "bss.manual_payment.approve",
        "bss.ledger.view", "bss.reconciliation.manage", "bss.report.view", "bss.audit.view",
    },
    "AUDITOR": {"bss.invoice.view", "bss.payment.view", "bss.ledger.view", "bss.report.view", "bss.audit.view"},
    "READ_ONLY": {"bss.invoice.view", "bss.payment.view", "bss.report.view"},
    "super_admin": {"*"},
}


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/bss"):
        return None
    if "/gateway-accounts" in path:
        return "bss.gateway.manage"
    if "/webhooks" in path:
        return "bss.webhook.view" if method == "GET" else "bss.webhook.view"
    if "/refunds" in path:
        return "bss.refund.approve" if method == "POST" and ("approve" in path or "/refunds/" in path) else "bss.payment.view"
    if "/manual-payments" in path:
        if path.endswith("/approve"):
            return "bss.manual_payment.approve"
        if path.endswith("/post"):
            return "bss.manual_payment.approve"
        if path.endswith("/submit") or method == "POST":
            return "bss.manual_payment.submit"
        return "bss.payment.view" if method == "GET" else "bss.manual_payment.approve"
    if "/reconciliation" in path or "/recon" in path:
        return "bss.reconciliation.manage" if method == "POST" else "bss.reconciliation.view"
    if "/dunning" in path:
        return "bss.dunning.manage" if method == "POST" else "bss.dunning.view"
    if "/ledger" in path:
        return "bss.ledger.view"
    if "/reports" in path:
        return "bss.report.view"
    if "/payment-intents" in path or "/payments" in path:
        return "bss.payment.capture" if method == "POST" and "capture" in path else "bss.payment.manage" if method == "POST" else "bss.payment.view"
    if "/invoices" in path:
        return "bss.invoice.view" if method == "GET" else "bss.invoice.manage"
    if "/audit" in path:
        return "bss.audit.view"
    return "bss.invoice.view"


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("BSS_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "management authentication failed")
    if len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired management token") from error
    required = management_permission(request.method, request.url.path)
    role = claims.get("role", "")
    permissions = set(claims.get("permissions", [])) | ROLE_PERMISSIONS.get(role, set())
    if required and "*" not in permissions and required not in permissions:
        raise HTTPException(403, "BSS permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role not in {"PLATFORM_ADMIN", "ISP_OWNER", "ISP_ADMIN", "super_admin"}:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    request.state.bss_principal = {"subject": claims.get("userId", claims.get("sub", "admin")), "role": role, "permissions": sorted(permissions)}


async def _json_tenant(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body.get("tenant_id") or body.get("tenantId")


def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-BSS-Service-Key", "")
    configured = getenv("BSS_INTERNAL_API_KEYS", getenv("BSS_INTERNAL_API_KEY", ""))
    expected = [value.strip() for value in configured.split(",") if value.strip()]
    if not expected or not any(secrets.compare_digest(item, supplied) for item in expected):
        raise HTTPException(401, "internal service authentication failed")
