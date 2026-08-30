# Assurance Service — Milestone 9 (Observability & Service Assurance)

The assurance-service is the **service-assurance, correlation, incident and
governance layer** of the ISP platform. It does **not** store raw metrics, logs
or traces — those live in Prometheus / Loki / Tempo behind an OpenTelemetry
Collector. This service manages the *governance surface*: service catalogue,
SLIs/SLOs/error budgets, alert lifecycle, incidents, root cause, postmortems,
KPIs, maintenance windows, synthetic checks and reporting.

## Boundaries

- **KEEP / EXTEND** — Correlation foundation (audit `correlation_id`,
  outbox/inbox envelopes with `tenant_id`, envelope `trace_context` slot).
- **ADD** — Alert/incident/SLI/SLO/KPI models, W3C trace context propagation
  (`shared/python/isp_shared/telemetry.py`), structured JSON logging, metric
  cardinality policy.
- **CONSUME (do not duplicate)** — Support/workforce SLAs stay in their source
  services; assurance consumes their events for change/impact correlation.
- **Config-only** — Prometheus/Grafana/Loki/Tempo/Alertmanager/OTel Collector
  are external infrastructure; this repo ships dashboards, alert rules and
  collector config under `infrastructure/`.

## Key concepts

- **Telemetry signals** are separate: METRIC / LOG / TRACE / DOMAIN_EVENT /
  NETWORK_EVENT / ALERT / INCIDENT.
- **W3C Trace Context** propagates across HTTP, RabbitMQ and Celery via the
  envelope `trace_context` slot; correlation IDs are stable across async
  boundaries.
- **Cardinality policy** — metric labels are restricted to controlled
  dimensions (`SAFE_LABELS` in `app/domain/identity.py`). Customer IDs,
  usernames, order/trace/session UUIDs, IPs and MACs are **rejected** as labels.
- **SLOs** are versioned; published versions are immutable. Error budgets and
  burn rates are reproducible from the stored window inputs + policy version.
- **Alerts** have stable fingerprints (no timestamps), dedup/grouping,
  inhibition, silencing, flapping detection, routing with fallback and
  notification delivery records.
- **Incidents** follow `DETECTED → TRIAGE → INVESTIGATING → IDENTIFIED →
  MITIGATING → MONITORING → RESOLVED → CLOSED` (+ POSTMORTEM_REQUIRED).
  Customer impact keeps **estimated vs confirmed** separate; support tickets
  are linked, never conflated.
- **Root cause** is evidence-based. Temporal coincidence is never auto-confirmed;
  `CONFIRMED_ROOT_CAUSE` requires ≥1 supporting evidence, no contradicting
  evidence, and explicit human confirmation.
- **Tenant-aware** — tenant-owned rows fail closed without a validated
  TenantContext; platform aggregates require explicit PLATFORM_AGGREGATE scope.

## Run

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r ../../shared/runtime/requirements.txt
pip install python-multipart==0.0.20
uvicorn app.main:app --host 0.0.0.0 --port 8009
```

Worker (SLO windows, alert expiry, silence cleanup, outbox flush):

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

Hermetic SQLite tests cover instrumentation, metrics/cardinality, SLI/SLO/error
budgets, alerts (dedup/grouping/inhibition/silencing/flapping/routing), incident
lifecycle, root cause, KPIs, multi-tenant isolation, consumers/idempotency, API
flows and an end-to-end scenario.
