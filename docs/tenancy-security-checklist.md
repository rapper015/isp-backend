# Tenancy Security Checklist — Milestone 8

## 1. Tenant context
- [x] `TenantContext` resolved only from trusted signals (JWT claim + membership); never from body/query alone.
- [x] Conflicting trusted signals reject the request (`TenantContextConflictError`).
- [x] Missing context **fails closed** (`TenantContextRequiredError`) — no silent default tenant, no empty-query fallback.
- [x] Context is immutable per request and cleared after request/task.

## 2. Database routing / isolation
- [x] `DatabaseRouter.db_for_read/write` raises for tenant-owned models without context.
- [x] No silent fallback: a `DATABASE_PER_TENANT` alias that isn't provisioned/READY fails.
- [x] Cross-tenant writes blocked (`assert_no_cross_tenant_write`).
- [ ] **Operator**: provision real tenant databases + routing for the `DATABASE_PER_TENANT` tier (control plane supports it; data-plane adoption is per-service).

## 3. RBAC & scopes
- [x] Action-level permission registry; deny-by-default.
- [x] Elevated permissions for tenant suspend/offboard, settlement approve/reversal, wallet adjust, payouts, impersonation, plan approval.
- [x] Scope-expansion denied (`ScopeExpansionError`); franchise admin cannot see sibling franchises.
- [x] Separation of duty enforced on maker-checker approvals and settlement approval.
- [x] Service accounts carry tenant scope + permission list + IP restrictions; credentials encrypted, expiring, rotatable, revocable.

## 4. Platform-admin access
- [x] Impersonation requires reason/ticket, read-only default, TTL, and emits audit + `tenancy.impersonation.started.v1`.
- [x] Platform aggregate access requires `PLATFORM_AGGREGATE` scope (tenant admins get 403).

## 5. Financial integrity
- [x] Commissions/settlements/wallets create **immutable ledger entries**; no editable balances as source of truth.
- [x] Earnings idempotent per (tenant, source event, rule); clawbacks/adjustments never delete originals.
- [x] Locked settlements cannot be edited; disputes resolve via adjustments.
- [x] Partner/tenant bank + integration secrets stored encrypted; masked in logs.
- [x] Commission engine executes only controlled rule types (no frontend-supplied formulas).

## 6. Events / messaging
- [x] Runtime envelopes carry `tenant_id`; consumers validate tenant exists + ACTIVE before acting.
- [x] `shared/contracts/event-envelope.schema.json` repaired to match (v2, `tenant_id`).
- [x] Duplicate events deduped; dead-letter/error preserves tenant context; context cleared after processing.

## 7. Observability
- [x] Append-only audit log (tenant, actor, effective user, impersonating user, before/after, reason, approval, correlation).
- [x] Tenant-aware Redis keys (cache/locks/rate limits scoped by tenant).
- [ ] **Operator**: set `TENANCY_JWT_SECRET`, `TENANCY_INTERNAL_API_KEY`, `TENANCY_ENCRYPTION_KEY` (all ≥32 chars); back encryption with KMS in production.

## 8. What is still required
- Per-service adoption of the tenant-context/routing contract (nms/ipam/siem/warehouse/aiops are not tenant-scoped today).
- Real RabbitMQ vhost separation and outbox broker publish hook (declared no-op like M5–M7).
- Backup encryption + per-tenant restore drills (runbook provided).
