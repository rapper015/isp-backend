# Dashboard Governance Guide

Dashboards are governed artifacts, not ad-hoc queries. Every dashboard has a
definition in the assurance-service (`DashboardDefinition`) plus a versioned
JSON in `infrastructure/observability/grafana/dashboards/`.

## Dashboards shipped

| UID | Audience | Purpose |
| --- | --- | --- |
| `ass-noc` | NOC | Service availability, firing alerts, 5xx rate, p95 latency, active incidents |
| `ass-sre` | SRE | SLO burn rate, remaining budget, SLI ratio (per SLO) |
| `ass-tenant` | Tenant ops | Tenant firing alerts, synthetic availability, SLI ratio |
| `ass-exec` | Executive | Platform health: tenants with alerts, major incidents (7d), overall availability |

## Data sources

- **Prometheus** (metrics) — default.
- **Loki** (logs).
- **Tempo** (traces).

## Governance rules

1. **Purpose + audience** — every dashboard declares `purpose`, `audience`,
   `owner`, `refresh_interval` and `tenant_scope`.
2. **Tenant-aware** — tenant dashboards filter by the `tenant` label; platform
   aggregates require explicit PLATFORM_AGGREGATE authorization at the API
   layer.
3. **Review cadence** — `DashboardDefinition.review_date`; dashboards without a
   recent review are flagged `DEPRECATION_STATUS`.
4. **Single source of truth** — edit the JSON under
   `infrastructure/observability/grafana/dashboards/`, then re-provision; the
   Grafana file provider syncs every 60s.
5. **Cardinality** — panels must only group by approved labels (see
   `alert-authoring-guide.md`).

## Monitoring the monitoring

- Prometheus scrapes itself; OTel Collector has `memory_limiter` (512 MiB) and
  `batch` processors.
- Alertmanager routes to the assurance ingest endpoint; delivery records
  (`NotificationDelivery`) confirm notifications were dispatched.
- Retention: Prometheus 15d, Tempo 48h, Loki default.

## Adding a dashboard

1. Create/register a `DashboardDefinition` (code, title, owner, audience).
2. Add the JSON panel file under `infrastructure/observability/grafana/dashboards/`.
3. Review labels for cardinality.
4. Test with the observability profile: `docker compose --profile observability up`.
