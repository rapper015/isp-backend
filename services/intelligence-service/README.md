# Intelligence Service — Milestone 10 (AI & Intelligence Layer)

A governed intelligence layer for the ISP platform. It builds the trusted
analytics/ML foundation, detects fraud and revenue leakage, predicts churn and
device/network failures, forecasts capacity, produces recommendations, and
manages a complete model lifecycle — **without ever mutating domain state
directly**.

## Safety boundary

ML models never directly modify RouterOS, FreeRADIUS, RADIUS sessions, customer
service status, financial ledgers, invoices/payments, IPAM, provisioning,
GenieACS device config, CPE firmware, customer lifecycle, tickets, or tenant
config. Models create predictions, risk signals, recommendations, fraud cases,
maintenance/capacity warnings, and **remediation intents**. Every operational
change passes through the authoritative service, policy validation, approval
and the domain saga/orchestration workflow.

Autonomy levels: **L0** insight · **L1** recommendation · **L2** intent +
approval · **L3** pre-approved, low-impact, reversible · **L4** prohibited by
default. Customer/finance/security/network/device actions default to L2 or
lower. A global + per-tenant kill switch, action budget, rate limit, cooldown,
circuit breaker, idempotency, blast-radius limits and complete audit trail
govern every automated action.

## Architecture

- `domain/` — statistics (pure-Python, no numpy/sklearn), feature transforms,
  fraud rules, churn bands, maintenance scoring, remediation safety.
- `models/` — `ai_` tables: data contracts/pipeline, feature store (offline +
  online), MLOps registry, AIOps (fraud/churn/maintenance/remediation).
- `services/` — ingestion, quality, features, datasets, ML lifecycle, fraud,
  churn, maintenance/capacity, recommendations, remediation.
- `messaging/consumers.py` — idempotent event ingestion.
- `tasks.py` + `worker_runner.py` — feature refresh, quality, drift, expiry,
  outbox flush.

## Model lifecycle

```
dataset snapshot → training run (time-based split, leakage check) →
evaluation (precision/recall/PR-AUC/ROC-AUC/ECE + baseline lift) →
model card → approval → SHADOW → CANARY → PRODUCTION → monitor → rollback/retire
```

Artifacts are **checksummed JSON config — never pickle** (no unsafe
deserialization). Per-tenant models, segment models, anonymized shared models
and global baselines with tenant calibration are supported; pooled training is
governed and documented, never silent.

## Run

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r ../../shared/runtime/requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Worker:

```bash
python -m app.worker_runner            # one cycle
python -c "from app.worker_runner import run_loop; run_loop(300)"   # loop
```

## Migrations

```bash
alembic upgrade head
```

## Tests

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

94 hermetic SQLite tests cover data foundation (contracts, dedup, quarantine,
late events, backfill/replay), point-in-time feature correctness, ML lifecycle
(training/registry/deploy/rollback/drift/model cards), fraud/churn/maintenance/
capacity use cases, remediation safety (approval, kill switch, budget, cooldown,
rate limit, circuit breaker, idempotency, cross-tenant blocking), multi-tenant
isolation and an end-to-end governed flow.

## Warehouse abstraction

The intelligence DB (Postgres) holds immutable raw events, analytical records,
offline features and the registry. Redis is an online feature cache only.
A `WAREHOUSE_ENGINE` env signals the future S3+Parquet / ClickHouse migration;
heavy analytics never run against production OLTP databases.
