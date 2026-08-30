# Assurance Service — Capacity & Scale Report

Scope: validate that the assurance governance layer can absorb the platform's
telemetry-derived records without becoming a bottleneck. This is a **design +
hermetic-test-based** assessment; no production load test was run (external
infra is required for that, see §59 of the spec).

## Design intent

- The assurance-service stores **governance records only** (normalized alerts,
  incidents, SLI measurements, SLO window states, KPI measurements, synthetic
  results). Raw metrics/logs/traces never enter its database — they are
  aggregated by Prometheus / Loki / Tempo behind the OTel Collector.
- This keeps DB write volume proportional to **event rate ÷ aggregation**, not
  raw sample rate.

## Measured (hermetic suite, SQLite)

- **94 tests pass** covering instrumentation, metrics/cardinality, SLI/SLO,
  error budgets, alerts (dedup/grouping/inhibition/silencing/flapping/routing),
  incidents, root cause, KPIs, multi-tenant isolation, consumers/idempotency,
  API flows and an end-to-end scenario.
- Full suite runs in ~2.5 min (SQLite, serial).

## Scaling model

| Record | Write pattern | Volume driver | Notes |
| --- | --- | --- | --- |
| `ass_sli_measurements` | per domain event (idempotent) | event rate | raw counts summed per window |
| `ass_slo_window_states` | one per SLO per window (worker) | # SLOs | deterministic, reproducible |
| `ass_alerts` | one per fingerprint; updates on dedup | alert events after dedup | dedup window coalesces repeats |
| `ass_incident_*` | human-initiated | incident rate | low volume |
| `ass_kpi_measurements` | periodic | # KPIs × period | period_key bucketed |
| `ass_synthetic_results` | per probe | probe rate | retain/archive policy advised |
| `ass_network_observations` | per observation | NMS/NAS events | retention policy advised |

### Throughput assumptions (design targets)

- Ingest of normalized alerts: target **> 50 alerts/sec** (pure SQLite insert +
  JSON; Postgres with psycopg is faster).
- Idempotent event consumption: O(1) dedup lookup via
  `ass_inbox_messages(consumer, event_id)` index — duplicate floods are dropped.
- SLO window computation: O(# measurements in window) per SLO; expected
  sub-second per SLO on Postgres.

### Cardinality control (prevents label explosion)

- `SAFE_LABELS` whitelist + high-cardinality rejection at ingest.
- Alert fingerprints are normalized (no timestamps) → bounded alert cardinality.
- OTel Collector `attributes/redact` drops customer/subscriber/order/IP/MAC
  labels before export.

## Known scaling constraints / genuine blockers

1. **No production load test** — external Prometheus/Loki/Tempo/Alertmanager
   stack is required to validate end-to-end capacity and collector backpressure.
   `docker compose --profile observability up` provisions it; nothing beyond
   config is in this repo.
2. **Indexed queries** — list endpoints sort by `first_observed` /
   `detected_at`; large historical retention needs the `Index` on
   `(tenant_id, state)` / `(sli_id, window_start, window_end)` already defined;
   archiving old windows is advised at scale.
3. **Retention** — `ass_synthetic_results` and `ass_network_observations` grow
   with probe/observation rate; a retention/archive job is recommended
   (out of scope for this milestone's worker).

## Recommendation

Run the observability profile, connect the exporters, and measure actual alert
and event ingest rates under representative synthetic load before declaring
capacity goals met.
