# Tenant Onboarding Runbook — Milestone 8

Onboarding is an **idempotent saga** executed by the tenancy-service. A tenant
is never marked `ACTIVE` before verification succeeds.

## 1. Request

```http
POST /api/tenancy/tenants
Authorization: Bearer <platform-jwt>
{
  "name": "Acme ISP",
  "code": "ACME",
  "currency": "INR",
  "country": "IN",
  "isolation_mode": "SHARED_SCHEMA_WITH_RLS"
}
```

Validation: unique name/code, valid code format, supported isolation mode.
Publishes `tenancy.tenant.requested.v1`.

## 2. Validate

```http
POST /api/tenancy/tenants/<tenant_id>/validate
```

## 3. Provision (saga)

```http
POST /api/tenancy/tenants/<tenant_id>/provision
```

Saga steps: `VALIDATING → PROVISIONING_CONTROL_RECORD → PROVISIONING_DATABASE →
RUNNING_MIGRATIONS → CREATING_STORAGE_NAMESPACE → CREATING_MESSAGING_NAMESPACE →
CONFIGURING_DEFAULTS → CREATING_ADMIN → VERIFYING → ACTIVE`.

During provisioning the service:
- creates the tenant database record (control plane; credential stored as an
  encrypted reference),
- records migration/admin health checks,
- installs defaults (permission registry, role templates, system roles, SoD
  constraints, feature flags, quotas, ledger accounts),
- applies default tenant configuration (legal/locale/portal/notifications/security),
- runs verification (database READY + features applied) before `ACTIVE`.

The saga is re-entrant: re-running on an ACTIVE tenant is a no-op; a FAILED
saga moves to `ROLLING_BACK`/`MANUAL_INTERVENTION_REQUIRED` (never silently
skipped).

## 4. Tenant administrator

Membership is separate from platform identity:

```http
POST /api/tenancy/tenants/<tenant_id>/memberships   { "user_id": "user@acme" }
POST /api/tenancy/tenants/<tenant_id>/roles          { "code": "TENANT_ADMIN", "name": "Tenant Admin" }
POST /api/tenancy/tenants/<tenant_id>/role-assignments
  { "membership_id": "<m>", "role_id": "<r>", "scope_kind": "TENANT" }
```

## 5. Configure

- Domains: `POST /tenants/{id}/domains` returns a verification token; the
  domain is **not** trusted for tenant resolution until verified.
- Branding/locale/currency/timezone: `PUT /tenants/{id}/config`.
- Feature flags: `POST /tenants/{id}/features` (entitlements, not auth).
- Quotas: `POST /tenants/{id}/quotas` (users, customers, NAS/CPE, storage, …).

## 6. Health

```http
GET /api/tenancy/tenants/<tenant_id>/health
```

## Operational notes

- Tenant suspension is scoped (`ADMIN_CONSOLE` / `NEW_CUSTOMER_ONBOARDING` /
  `BILLING` / `NETWORK_SERVICE` / full shutdown) and **never** auto-disconnects
  subscribers or auto-suspends partners unless an explicitly approved policy
  requires it.
- Offboarding is a separate, recoverable flow (see the offboarding runbook).
