# Milestone 9 — Assurance API (Observability & Service Assurance)

Service: **`assurance-service`** · Base path: `/api/assurance/v1` (gateway
`/api/v1/assurance/`) · DB prefix: `ass_` · Exchange: `assurance.events.v1`

The assurance-service is the **governance layer**. It does not store raw
metrics, logs or traces (those live in Prometheus / Loki / Tempo behind the
OpenTelemetry Collector). It manages the service catalogue, SLIs/SLOs/error
budgets, alert lifecycle, incidents, root cause, postmortems, KPIs, maintenance
windows, synthetic checks and reports.

## Auth

- Management JWT: `Authorization: Bearer <JWT>` signed with `ASSURANCE_JWT_SECRET`
  (≥32 chars). Claims: `userId`, `role`, `permissions`, `tenant_id`, `scope_kind`.
- Internal ingest: `X-Internal-API-Key` (`ASSURANCE_INTERNAL_API_KEY`).
- Roles: `PLATFORM_ADMIN`, `NOC_ENGINEER`, `SRE_PLATFORM`, `SRE_ENGINEER`,
  `SECURITY_OPS`, `AUDITOR`, `READ_ONLY`, `TENANT_ADMIN`.
- Elevated permissions: incident close/declare, SLO approve/activate, maintenance
  approve, postmortem approve, root-cause confirm, platform aggregates.
- **Tenant isolation fails closed.** Tenant-owned data requires a validated
  tenant in the JWT; platform aggregates require `scope_kind: PLATFORM_AGGREGATE`.

## Internal ingest (service-to-service)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/internal/assurance/v1/ingest/alert` | Normalize an inbound alert (service, alert_name, severity, component, resource, labels, impact) → stable fingerprint, dedup/group, route. |
| POST | `/internal/assurance/v1/ingest/observation` | Record a network observation (device_ref, check_type, status, latency, metrics). |
| POST | `/internal/assurance/v1/ingest/event` | Consume a domain event envelope (idempotent) → SLI/KPI measurements, change events, network observations. |

## Service catalogue

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/services` | List service definitions (code, name, criticality, tier, owner_team, status). |
| POST | `/services` | Create a service definition. |

## SLI / SLO / error budgets

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/slis` | List SLI definitions. |
| POST | `/slis` | Create an SLI. |
| POST | `/sli-measurements` | Record an SLI measurement (good/total, window, quality, exclusions). |
| POST | `/slos` | Create an SLO + first version (immutable when published). |
| GET | `/slos` | List SLOs with latest version. |
| POST | `/slos/{id}/validate` | DRAFT → VALIDATING. |
| POST | `/slos/{id}/approve` | VALIDATING → APPROVED (elevated). |
| POST | `/slos/{id}/activate` | APPROVED/DISABLED → ACTIVE (elevated). |
| GET | `/slos/{id}/error-budget` | Current error budget + burn rate for the active window. |
| POST | `/slos/{id}/compute-window` | (Re)compute the current window → `SloWindowState` (reproducible). |

## Maintenance windows

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/maintenance` | Request a maintenance window. |
| GET | `/maintenance` | List maintenance windows. |
| POST | `/maintenance/{id}/approve` | REQUESTED → APPROVED (elevated). |
| POST | `/maintenance/{id}/cancel` | Cancel. |
| POST | `/maintenance/{id}/exceptions` | Add an SLO maintenance exception. |

## Alerts

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/alerts` | List alerts (filter by state/service). |
| POST | `/alerts/{id}/acknowledge` | ACK a firing alert. |
| POST | `/alerts/{id}/resolve` | Resolve an alert (publishes `assurance.alert_resolved.v1`). |
| POST | `/alerts/{id}/expire` | Expire a stale alert. |
| POST | `/silences` | Create an alert silence. |
| GET | `/silences` | List silences. |
| POST | `/silences/{id}/cancel` | Cancel a silence. |
| GET | `/alert-routes` | List routing rules. |
| POST | `/alert-routes` | Create a routing rule (match labels → channel/recipients, fallback). |

## Incidents

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/incidents` | List incidents (filter by state). |
| POST | `/incidents` | Create an incident (optionally from an alert). |
| GET | `/incidents/{id}` | Incident detail + timeline + impact summary. |
| POST | `/incidents/{id}/transition` | State transition (DETECTED→…→RESOLVED/CLOSED, POSTMORTEM_REQUIRED). |
| POST | `/incidents/{id}/major` | Declare a major incident. |
| POST | `/incidents/{id}/commanders` | Assign a commander. |
| POST | `/incidents/{id}/responders` | Add a responder. |
| POST | `/incidents/{id}/alerts` | Link an alert. |
| POST | `/incidents/{id}/tickets` | Link a support ticket (never conflated with the incident). |
| POST | `/incidents/{id}/service-impact` | Add affected service. |
| POST | `/incidents/{id}/impact-estimate` | Record **estimated** customer impact. |
| POST | `/incidents/{id}/impact-confirm` | Record **confirmed** customer impact (separate from estimate). |
| POST | `/incidents/{id}/communications` | Add a communication (INTERNAL / CUSTOMER_SAFE). |
| POST | `/incidents/{id}/actions` | Add an incident action. |
| POST | `/incidents/{id}/require-postmortem` | Move to POSTMORTEM_REQUIRED. |

## Root cause

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/incidents/{id}/root-causes` | Create a hypothesis (OBSERVATION; `is_ai_suggestion` supported). |
| POST | `/root-causes/{id}/evidence` | Attach supporting / contradicting evidence. |
| POST | `/root-causes/{id}/transition` | OBSERVATION → HYPOTHESIS → LIKELY_CAUSE → … |
| POST | `/root-causes/{id}/confirm` | Confirm root cause (requires ≥1 supporting evidence, no contradicting, explicit human). |
| GET | `/incidents/{id}/root-causes` | List hypotheses for an incident. |

## Postmortems

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/postmortems` | Create a postmortem for an incident in POSTMORTEM_REQUIRED. |
| POST | `/postmortems/{id}/actions` | Add a postmortem action item. |
| GET | `/postmortems` | List postmortems. |

## KPIs & synthetic

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/kpis` | List KPI definitions + latest measurement. |
| POST | `/kpis` | Create a KPI definition. |
| POST | `/kpi-measurements` | Record a KPI measurement. |
| POST | `/kpi-targets` | Set a KPI target. |
| GET | `/synthetic` | List synthetic checks. |
| POST | `/synthetic` | Create a synthetic check. |
| POST | `/synthetic/results` | Record a synthetic result. |

## Dashboards & reports

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/dashboards/tenant` | Tenant dashboard (firing alerts, active incidents, SLI ratio, synthetic availability). |
| GET | `/reports/incidents` | Incident report by severity over N days. |
| GET | `/reports/slo-budgets` | SLO budget report for the tenant. |
| GET | `/dashboards/platform` | **Platform aggregate** (cross-tenant; requires PLATFORM_AGGREGATE scope). |
| GET | `/reports/aggregate` | **Platform aggregate** report (same authorization). |
| GET | `/audit-log` | Append-only audit log. |
| GET | `/metric-registry` | Registered metric dimensions (cardinality policy). |

## Events

**Published** (outbox, `assurance.events.v1`):
`assurance.alert_normalized.v1`, `assurance.alert_resolved.v1`,
`assurance.incident_created.v1`, `assurance.incident_updated.v1`,
`assurance.incident_resolved.v1`, `assurance.customer_impact_detected.v1`,
`assurance.slo_at_risk.v1`, `assurance.slo_breached.v1`,
`assurance.error_budget_exhausted.v1`,
`assurance.root_cause_hypothesis_created.v1`,
`assurance.root_cause_confirmed.v1`, `assurance.postmortem_required.v1`,
`assurance.maintenance_window_approved.v1`.

**Consumed** (idempotent, mapped to SLI/KPI measurements, change events and
network observations): `oss.order.*`, `billing.payment.*`, `crm.customer.*`,
`ticket.*`, `workforce.job.*`, `device.cpe.*`, `tenancy.tenant.*`,
`aaa.session.stale.v1`, `nas.health_changed.v1`, `firmware.rollout.started.v1`,
`network.policy.*`, `configuration.profile.changed.v1`.

All envelopes carry `tenant_id`, `correlation_id`, `causation_id`,
`idempotency_key` and the W3C `trace_context` slot.
