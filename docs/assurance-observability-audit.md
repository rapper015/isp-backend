# Milestone 9 — Observability & Service Assurance: Architecture Audit

Service: `assurance-service` (new governance layer). Date: 2026-08-30.

## 1. What already exists

| Area | Exists | Location |
| --- | --- | --- |
| Logging | Plain-text only (Python/uvicorn defaults); **no JSON formatter, no LOG_LEVEL/LOG_JSON handling** | no code configures logging; `LOG_LEVEL` env set in docker-compose but unused |
| Metrics | **One** in-process counter module (AAA) | `aaa-service/app/metrics.py`, exposed at `/internal/radius/v1/metrics` |
| Tracing | **None** (no OpenTelemetry packages) | `trace_context: {}` envelope slot always empty |
| Correlation | Strong foundation: `correlation_id` in audit/outbox/domain tables + event envelopes; `causation_id` accepted by device-mgmt/tenancy | each service `events.py`/`audit_service.py` |
| Health | Uniform `GET /health` + `GET /status`; only AAA has a DB-check readiness | each `app/main.py`; docker healthcheck polls `/health` |
| NMS | Stub (`nas_devices`, `health_observations`); real logic in legacy Django + AAA `nas_health_checks`/`nc_router_readiness` | `nms-service/app/models.py`; `aaa-service/app/nas_service.py` |
| RabbitMQ/Redis/DB monitoring | None (no queue-depth, no pg_stat, no synthetic probes) | — |
| Alerts / Incidents / SLA | **None** in modern services (CRM only `Lead.sla_deadline`; support/workforce SLA source missing) | — |
| Dashboards / Grafana / Prometheus / Loki / Tempo / Alertmanager | **None** | no observability infra config anywhere |
| Events | Outbox/inbox + envelope carry tenant/correlation/causation; exchange per service | each service `events.py`; `shared/contracts/event-envelope.schema.json` v2 |
| Audit | Append-only `AuditLog` per service | each `app/models/messaging.py` |

## 2. Classification

| Component | Decision | Notes |
| --- | --- | --- |
| AAA in-process metrics | `EXTEND` | Move to a shared telemetry library; keep `/metrics` contract; add Prometheus exposition via shared lib |
| Correlation plumbing (correlation_id/causation_id) | `KEEP` + `EXTEND` | Populate the empty `trace_context` envelope slot with W3C trace context via a shared library |
| `GET /health` + `/status` | `EXTEND` | Add separate liveness/readiness + dependency status; never mark platform down for one device |
| NMS stub + AAA nas_health_checks | `EXTEND` | assurance-service consumes NMS/network observations + synthetic results; NMS remains the collector contract |
| Legacy Django monitoring | `KEEP` (deprecate) | Preserved; not the target |
| Event envelope | `EXTEND` | Propagate trace context across HTTP/RabbitMQ via shared carrier helpers |
| Outbox/inbox | `KEEP` | assurance-service reuses the transactional outbox + idempotent inbox |
| Support/workforce SLA timers | `CONSUME` (do not duplicate) | assurance-service consumes authoritative SLA events + produces consolidated projections |
| Grafana/Prometheus/Loki/Tempo/Alertmanager | `ADD` (config) | Deployed as infrastructure; Python is the assurance/governance layer, never a metrics/log/trace store |

## 3. Vulnerability / gap scan

- **Plain-text unstructured logs** everywhere; no correlation/tenant in log records; no centralized redaction.
- **No trace context across RabbitMQ** — `trace_context: {}` in every envelope.
- **No RED metrics** for APIs/workers; only AAA counters.
- **No alert owner/runbook/severity model**; no dedup/grouping/inhibition; no incident model; no maintenance windows.
- **No SLI/SLO/error-budget engine**; support/workforce SLA computed only in their (missing) sources.
- **NMS not a real monitoring service**; no synthetic probes; no capability-aware collection.
- **No observability infra** — nothing to protect, but also nothing to query.
- **No retention policies** for telemetry.

## 4. Selected architecture

OpenTelemetry-based stack with the Python platform as the **assurance/governance layer** (it never stores raw logs/metrics/traces — those live in Prometheus/Loki/Tempo via an OTel collector). The `assurance-service` owns:

- Service catalogue (definitions/components/dependencies/owners/topology/impact rules)
- Versioned SLI/SLO definitions + reproducible SLO windows + error budgets/burn rates
- Maintenance windows + exceptions
- Alert definitions + normalization (stable fingerprints, dedup, grouping, inhibition, silencing, routing) + notification delivery records
- Incidents (lifecycle, commander/responders, alerts, service/customer impact, tickets, communications, timeline, actions)
- Root-cause evidence framework (observation/hypothesis/likely/confirmed/rejected with supporting+contradicting evidence)
- Postmortems + action items
- Versioned KPI definitions + measurements + targets + quality
- Change events (deployments/config/firmware) for correlation
- Synthetic check definitions + results
- Dashboard governance metadata
- Tenant-aware alerts/incidents/reports; platform aggregate only under explicit authorization

## 5. Telemetry separation (enforced)

Metrics (numeric, cardinality-policy labels) ≠ Logs (structured JSON with correlation) ≠ Traces (W3C context across HTTP/RabbitMQ) ≠ Domain events (durable outbox) ≠ Network events ≠ Alerts (normalized, deduped) ≠ Incidents (coordinated response). No log == alert == incident.

## 6. Key invariants (implemented)

- Sensitive data (passwords, RADIUS/RouterOS/GenieACS secrets, payment payloads, full KYC, unredacted contact info) is never placed in telemetry labels/baggage/logs; centralized redaction + safe-label cardinality policy.
- SLO calculations are reproducible (versioned policy, stored window inputs); maintenance exclusions are scoped + audited and preserve raw measurements.
- Temporal coincidence is never auto-marked confirmed root cause — evidence states + human confirmation.
- Alerts are grouped/deduped and separate from incidents; incidents separate from support tickets but linkable.
- Observability backends are not exposed publicly; tenant-scoped queries; cross-tenant views require explicit authorization and are audited.
