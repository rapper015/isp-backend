# Intelligence Layer — Repository Audit & Classification (Milestone 10)

Audit performed against the full monorepo at `d:\Codes\Client\ISP-MAIN\isp-backend`
before implementing the AI & Intelligence Layer. The goal is a governed
intelligence layer that builds on existing infrastructure without bypassing
domain ownership.

## 1. Existing AI / analytics / reporting functionality

| Component | State | Notes |
| --- | --- | --- |
| `services/aiops-service/` | **SKELETON** | `app/main.py` health/status only (`phase: "foundation"`). No models, events, tests, migrations. |
| `services/warehouse-service/` | **SKELETON + dead legacy** | Health/status skeleton. `legacy/dashboard/` is dead Django referencing nonexistent legacy models (`aaa.AccountingRecord`, `billing.Invoice`, `customers.Customer`…). No analytical store. |
| `services/assurance-service/` | **FULL (M9)** | 68 routes; 13 published / 18 consumed events; `ass_` tables; the convention template to copy. |
| `workforce`, `support`, `nms`, `ipam`, `siem` | **SKELETONS** | Health/status only; some stale `.pyc` only. |

**Conclusion:** the intelligence layer is effectively greenfield, but a rich,
consistent event / outbox / tenant / audit / worker scaffold exists to build on.

## 2. Event bus (RabbitMQ) — exchanges & topologies

| Service | Exchange | Publishes |
| --- | --- | --- |
| aaa | `aaa.events.v1` | `nas.*`, `policy.*`, `fup.*`, `session.*`, `coa.*`, `router.*`, `network.identity_assigned.v1` |
| crm | `crm.events.v1` | `crm.lead.*`, `crm.customer.*`, `crm.kyc.*`, `crm.caf.approved.v1`, `crm.customer.lifecycle_changed/risk_changed.v1` |
| oss | `oss.events.v1` | `oss.order.*`, `oss.service.*`, `oss.resource.*`, `oss.workflow.*` |
| bss | `bss.events.v1` | `payment.*`, `settlement.*`, `reconciliation.*`, `billing.account_delinquent/suspension_required/restoration_eligible.v1`, `dunning.*`, `invoice.issued/overdue.v1` |
| device | `device.events.v1` | `cpe.*` (17) |
| tenancy | `tenancy.events.v1` | `tenancy.tenant.*`, `partner.*`, `commission.*`, `settlement.*`, `wallet.entry.v1`, `customer.transferred.v1`, `ownership.changed.v1` |
| assurance | `assurance.events.v1` | `assurance.*` (13) |

**Envelope v2** (`shared/contracts/event-envelope.schema.json`): required
`event_id`, `event_type`, `occurred_at`, `producer`, `payload`; optional
`schema_version`, `tenant_id`, `correlation_id`, `causation_id`,
`idempotency_key`, `trace_context`.

## 3. Outbox / inbox / idempotency (KEEP + reuse)

Transactional outbox per service; `consume_once` dedup via
`*_inbox_messages(consumer, event_id)`; `unprocessed_events` flushed by worker.
The intelligence service will consume the full published topology above and
publish `ai.*` events via its own outbox.

## 4. Tenant isolation (KEEP + reuse)

Frozen `TenantContext` in a ContextVar; `require_tenant()` fail-closed;
`resolve()` conflicts rejected; `enforce_scope()` fail-closed;
`require_platform_aggregate()` for platform aggregates. Intelligence layer will
apply the same to datasets, features, models (per-tenant), predictions and
remediation.

## 5. Audit logging (KEEP + reuse)

Append-only `*_audit_log` (actor, action, resource, before/after JSON, reason,
correlation_id, occurred_at). Intelligence service adds its own `ai_audit_log`.

## 6. Workers & schedulers (KEEP + reuse)

Poll-loop `worker_runner.run_once/run_loop` convention; docker-compose
`*-worker` services run `python -m app.worker_runner`. No Celery.

## 7. Storage for telemetry / historical data (GAP → design decision)

- No object store, ClickHouse, DuckDB, or OLAP store exists anywhere.
- Assurance policy: raw metrics/logs/traces live in Prometheus/Loki/Tempo via
  OTel Collector; Python services keep **aggregated/governance** records only.
- **Decision for M10:** Postgres (`intelligence` DB) holds immutable raw event
  records, normalized analytical records, offline features, and the model
  registry. Redis holds short-lived online feature cache. A `warehouse`
  abstraction documents the future S3+Parquet / ClickHouse migration; no heavy
  analytics runs against OLTP databases — ingestion is event-driven and batch
  via the outbox pipeline.

## 8. Shared lib & dependencies

`shared/python/isp_shared/telemetry.py` (pure stdlib W3C trace context +
structured JSON logging + redaction) is ready to wire. No ML libraries are
pinned; **M10 uses pure-Python statistical/rule baselines** (EWMA, z-score,
weighted-logit scoring, moving-average forecast) — no sklearn/numpy required —
and stores model artifacts as **checksummed JSON config, never pickle**.

## 9. Classification summary

| Item | Classification | Action |
| --- | --- | --- |
| `aiops-service` skeleton | **DEPRECATE → replace** | Superseded by `intelligence-service` (full implementation). |
| `warehouse-service` skeleton | **EXTEND (abstraction only)** | Warehouse abstraction + docs; legacy dashboard dead code marked DEPRECATE. |
| Event envelope + outbox/inbox | **KEEP + reuse** | Adopt unchanged. |
| TenantContext / routing fail-closed | **KEEP + reuse** | Adopt unchanged. |
| AuditLog append-only | **KEEP + reuse** | Adopt unchanged. |
| Worker poll-loop convention | **KEEP + reuse** | Adopt unchanged. |
| `isp_shared/telemetry.py` | **EXTEND** | Wire trace_context + structured logging into intelligence service. |
| Observability stack (M9) | **KEEP + extend** | AI monitoring emits metrics/logs; reuse Prometheus/Loki/Tempo config. |
| No analytical store | **ADD (documented abstraction)** | Postgres-first, warehouse abstraction for S3/Parquet/ClickHouse migration. |

## 10. New service: `services/intelligence-service`

- Table prefix `ai_`; exchange `intelligence.events.v1`.
- Consumes: `crm.customer.*`, `crm.lead.*`, `oss.order.*`, `billing.payment.*`,
  `billing.invoice.*`, `billing.account_delinquent.v1`, `aaa.session.*`,
  `nas.health_changed.v1`, `network.identity_assigned.v1`, `device.cpe.*`,
  `tenancy.tenant.*`, `assurance.alert_*.v1`, `assurance.incident_*.v1`,
  `assurance.customer_impact_detected.v1`, `assurance.slo_*.v1`.
- Publishes `ai.*` events (dataset_ready, training_completed, model_approved,
  model_deployed, model_rolled_back, prediction_created, fraud_signal_detected,
  churn_risk_updated, failure_risk_detected, capacity_risk_detected,
  recommendation_created, remediation_requested/approved/rejected/started/
  completed/failed, model_drift_detected).
- Modules: `domain` (fraud, churn, maintenance, remediation safety),
  `models` (contracts/pipeline, features, mlops, aiops, messaging),
  `services` (ingestion, quality, features, ml lifecycle, fraud, churn,
  maintenance, capacity, recommendations, remediation), `messaging/consumers`,
  `tasks` + `worker_runner`.
