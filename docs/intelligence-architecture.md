# Intelligence Layer — Architecture Decision Record (Milestone 10)

Status: **Accepted** · Date: 2026-08-30

## Context

The platform has event/outbox/tenant/audit/worker conventions (Milestones 0–9)
but **no** AI/analytics functionality: `aiops-service` and `warehouse-service`
are skeletons. M10 must add a governed intelligence layer that builds on
existing infra, preserves tenant isolation, and never bypasses domain
ownership.

## Decisions

### D1 — Single `intelligence-service` (not multiple new services)
The spec's `data_intelligence/feature_store/mlops/aiops` are **modules inside
one microservice** (`services/intelligence-service`, prefix `ai_`), matching the
monorepo's one-service-per-domain convention. This avoids the multi-service
operational cost of a speculative split.

### D2 — Postgres-first analytical store; warehouse abstraction
No object store / ClickHouse exists. Decision: the intelligence Postgres DB
holds immutable raw events, analytical records, offline features, dataset
snapshots and the model registry. Redis is an online feature cache only.
`WAREHOUSE_ENGINE` documents the future S3+Parquet / ClickHouse migration.
Heavy analytics never run against production OLTP databases.

### D3 — Pure-Python statistical baselines, no heavy ML deps
Fraud/churn/maintenance/capacity use **rule + statistical baselines** written
against `shared/runtime/requirements.txt` (no numpy/sklearn). This satisfies
the "working baselines" definition of done and keeps the dependency footprint
small. Artifacts are **checksummed JSON config** — never pickle (avoids unsafe
deserialization). A richer framework (e.g. MLflow) can be added behind the
registry abstraction later.

### D4 — W3C trace context + structured logs via `isp_shared/telemetry.py`
The intelligence service wires the shared telemetry lib (traceparent,
correlation ContextVars, JSON logging + redaction). Envelope `trace_context`
carries propagation across RabbitMQ.

### D5 — Safety-first remediation
Autonomy L0–L4; high-impact categories (customer/finance/security/network/
device) default to ≤L2; L3 reserved for low-impact reversible actions. Global +
tenant kill switch; budget/rate-limit/cooldown/circuit-breaker/idempotency/
blast-radius gates; full audit trail. Cross-tenant actions are rejected.

### D6 — MLOps lifecycle on the registry tables
Time-based splits + leakage check (never random-only for time-series);
evaluation includes precision/recall/PR-AUC/ROC-AUC/ECE plus baseline-lift;
approval → shadow/canary/production → monitor (drift) → rollback/retire.
Pooled/anonymized training is governed and documented, never silent.

### D7 — Reuse existing conventions unchanged
Transactional outbox + idempotent inbox, fail-closed TenantContext, append-only
audit, poll-loop worker, alembic additive migration, hermetic SQLite tests,
gateway route, docker-compose service+worker.

## Consequences

- One new deployable (`intelligence-service` + `intelligence-worker`) and one
  new DB (`intelligence`).
- Baselines are honest: complex models must demonstrate lift over them.
- No unsupported claim of "self-healing" — automated actions are bounded,
  reversible and governed.
