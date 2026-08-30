# Milestone 10 — Intelligence API (AI & Intelligence Layer)

Service: **`intelligence-service`** · Base path: `/api/intelligence/v1`
(gateway `/api/v1/intelligence/`) · DB prefix: `ai_` · Exchange:
`intelligence.events.v1`

The intelligence layer is the **governed AI surface**. It never mutates domain
state directly — operational changes become remediation intents that pass
policy evaluation + approval to the authoritative service.

## Auth

- Management JWT: `Authorization: Bearer <JWT>` signed with
  `INTELLIGENCE_JWT_SECRET` (≥32 chars). Claims: `userId`, `role`,
  `permissions`, `tenant_id`, `scope_kind`.
- Internal ingest: `X-Internal-API-Key` (`INTELLIGENCE_INTERNAL_API_KEY`).
- Roles: `PLATFORM_ADMIN`, `AI_ENGINEER`, `MLOPS_ENGINEER`, `DATA_SCIENTIST`,
  `NOC_ENGINEER`, `SRE_PLATFORM`, `SECURITY_OPS`, `CRM_RETENTION`,
  `FINANCE_OPS`, `AUDITOR`, `READ_ONLY`, `TENANT_ADMIN`.
- Elevated: model approve, deploy/rollback/retire, kill switch,
  remediation manage, fraud manage, platform aggregates, retention manage.
- **Tenant isolation fails closed**; platform aggregates require
  `scope_kind: PLATFORM_AGGREGATE`.

## Data foundation

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/internal/intelligence/v1/ingest/event` | Idempotent domain-event ingestion (raw → analytical, quarantine on contract violation). |
| GET | `/contracts` | Versioned data contracts (required/optional/PII fields, retention, owner). |
| POST | `/contracts` | Register a contract. |
| POST | `/ingest` | Ingest an envelope via API (validates + quarantines). |
| GET | `/raw-events` | Immutable raw events (filter by contract/state). |
| GET | `/quality` | Data-quality results. |
| POST | `/quality/run` | Run quality checks for a contract (completeness/freshness/uniqueness/schema). |
| POST | `/replay` | Idempotent backfill/replay raw events → analytical records. |
| GET | `/datasets` | Dataset snapshots. |
| POST | `/datasets` | Snapshot a dataset (row count + checksum). |
| POST | `/datasets/{id}/approve` | Approve a training dataset (governed). |

## Features

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/features` | Versioned feature definitions. |
| POST | `/features` | Register a feature definition. |
| GET | `/features/values` | Online feature vector for an entity (freshness-marked). |

## MLOps

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/training` | Train + register a model version (time-based split, leakage check, metrics, model card). |
| GET | `/training` | Training-run history. |
| GET | `/models` | Model registry. |
| POST | `/models/{id}/approve` | Approve (elevated). |
| POST | `/models/{id}/deploy` | Deploy SHADOW / CANARY / PRODUCTION (elevated). |
| POST | `/models/{id}/rollback` | Rollback to previous version (elevated). |
| POST | `/models/{id}/retire` | Retire / archive (elevated). |
| POST | `/models/{id}/monitor` | Record a monitor metric. |
| GET | `/monitoring` | Model health (drift, latency, alerts). |
| POST | `/models/{id}/drift` | Detect prediction drift (alerts on threshold breach). |

## Use cases

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/fraud/rules` | Deterministic fraud rules. |
| POST | `/fraud/evaluate` | Evaluate record → fraud signal(s) (rules + optional model score). |
| GET | `/fraud/signals` | Fraud signals. |
| POST | `/fraud/cases` | Open a fraud case from signals. |
| GET | `/fraud/cases` | Fraud cases. |
| POST | `/fraud/cases/{id}/decision` | Record a review decision (never auto-suspension). |
| POST | `/fraud/cases/{id}/transition` | OPEN → IN_REVIEW → APPROVED/REJECTED → CLOSED. |
| POST | `/fraud/cases/{id}/recommend` | Fraud action recommendation (target service owns execution). |
| POST | `/churn/score` | Churn-risk score (horizon, drivers, model version, expiry). |
| GET | `/churn` | Churn scores by risk band. |
| POST | `/retention/candidates` | Create a retention candidate. |
| POST | `/retention/candidates/{id}/track` | Track offer/consent/outcome/experiment. |
| POST | `/maintenance/predict` | Failure probability + recommendation. |
| GET | `/maintenance` | Active failure predictions. |
| POST | `/capacity/forecast` | Capacity forecast with confidence interval + risk. |
| GET | `/capacity` | Capacity forecasts. |

## Recommendations & remediation

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/recommendations` | Create a recommendation (L0–L4; evidence + model version). |
| GET | `/recommendations` | List recommendations. |
| GET | `/remediation/policies` | Autonomy policies (budget, cooldown, blast radius, reversible). |
| POST | `/remediation/intents` | Create a remediation intent (idempotent; kill-switch + precondition gated). |
| GET | `/remediation/intents` | List intents. |
| POST | `/remediation/intents/{id}/approve` | Approve (elevated; self-approval rejected). |
| POST | `/remediation/intents/{id}/reject` | Reject. |
| POST | `/remediation/intents/{id}/execute` | Execute (budget/rate/cooldown/circuit/approval gates). |
| POST | `/remediation/intents/{id}/complete` | Complete with verification. |
| POST | `/remediation/intents/{id}/fail` | Fail / compensate. |
| GET | `/kill-switch` | Global + tenant kill-switch state. |
| POST | `/kill-switch` | Engage/release kill switch (elevated). |

## Insights

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/insights` | Tenant insights (fraud, churn, maintenance, recommendations, remediation). |
| GET | `/reports/executive` | **Platform aggregate** (requires PLATFORM_AGGREGATE). |
| GET | `/audit-log` | Append-only audit log. |

## Events

**Published** (outbox, `intelligence.events.v1`): `ai.dataset_ready.v1`,
`ai.data_quality_failed.v1`, `ai.training_completed.v1`, `ai.model_approved.v1`,
`ai.model_rejected.v1`, `ai.model_deployed.v1`, `ai.model_rolled_back.v1`,
`ai.prediction_created.v1`, `ai.fraud_signal_detected.v1`,
`ai.churn_risk_updated.v1`, `ai.failure_risk_detected.v1`,
`ai.capacity_risk_detected.v1`, `ai.recommendation_created.v1`,
`ai.remediation_requested.v1`, `ai.remediation_approved.v1`,
`ai.remediation_rejected.v1`, `ai.remediation_started.v1`,
`ai.remediation_completed.v1`, `ai.remediation_failed.v1`,
`ai.remediation_compensated.v1`, `ai.model_drift_detected.v1`,
`ai.kill_switch_engaged.v1`.

**Consumed** (idempotent, mapped to contracts): `crm.customer.*`, `crm.lead.*`,
`oss.order.*`, `oss.service.*`, `billing.invoice.*`, `billing.payment.*`,
`billing.account_delinquent.v1`, `aaa.session.*`, `nas.health_changed.v1`,
`nas.radius_registration.v1`, `network.identity_assigned.v1`,
`device.cpe.*`, `tenancy.tenant.*`, `assurance.alert_*.v1`,
`assurance.incident_*.v1`, `assurance.customer_impact_detected.v1`,
`assurance.slo_*.v1`. Envelopes carry `tenant_id`, `correlation_id`,
`causation_id`, `idempotency_key` and the W3C `trace_context` slot.
