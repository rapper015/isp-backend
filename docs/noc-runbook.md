# NOC Runbook — Service Assurance

This runbook is for NOC engineers operating the ISP platform through the
assurance-service and the observability stack (Prometheus, Grafana, Loki,
Tempo, Alertmanager, OTel Collector).

## Topology

```
services ──/internal/metrics──▶ Prometheus ──▶ Alertmanager ──▶ assurance-service
                                                                │  (ingest/alert)
services ──OTLP──▶ OTel Collector ──▶ Prometheus (metrics)
                                      Loki (logs)
                                      Tempo (traces)
                                  ──▶ Grafana (NOC / SRE / tenant / executive dashboards)
```

Raw telemetry is **never** stored in the Python service. The assurance-service
holds the governance surface (alerts, incidents, SLOs, KPIs, synthetic checks).

## Initial triage

1. Open the **NOC dashboard** (`ass-noc`): Services Up/Down, firing alerts,
   5xx error rate, p95 latency, active incidents.
2. For each FIRING alert, open the alert record:
   `GET /api/assurance/v1/alerts?state=FIRING`.
3. Acknowledge:
   `POST /api/assurance/v1/alerts/{id}/acknowledge` (JWT, `alerts.ack`).
4. If customer impact is likely, create an incident:
   `POST /api/assurance/v1/incidents` with the alert id.

## Service down

- `GET /status` on the affected service for liveness/readiness + DB.
- Check Prometheus `up{job="isp-services"}` and the 5xx rate.
- Escalate per alert severity:
  - CRITICAL → page NOC on-call, declare major if customer-facing.
  - HIGH → page, investigate within 15 min.
  - MEDIUM/LOW → queue, acknowledge.

## Alert lifecycle

PENDING → FIRING → ACKNOWLEDGED → RESOLVED (or SUPPRESSED / SILENCED / EXPIRED).

- **Silencing** (planned change): create a silence with matching labels and a
  window before the change.
- **Suppression/inhibition**: downstream alerts on the same component are
  auto-suppressed while a parent (e.g. POP router) is firing. Do not
  double-page; investigate the parent.
- **Flapping**: oscillating alerts are auto-suppressed to reduce noise.

## SLO burn

- Open the **SRE dashboard** (`ass-sre`) → burn rate + remaining budget.
- Fast burn (`burn_rate ≥ 14.4`): treat as CRITICAL — budget exhausted within
  ~2 days of a 30d window.
- Slow burn (`1.0 ≤ burn_rate < 14.4`): MEDIUM — review capacity.
- `GET /api/assurance/v1/slos/{id}/error-budget` for the exact numbers.
- A maintenance window (approved) excludes its SLO events from the contractual
  window; raw measurements are preserved.

## Synthetic checks

- `POST /api/assurance/v1/synthetic` to register checks (LOGIN,
  PORTAL_AVAILABILITY, RADIUS_AUTH, ROUTEROS_READINESS, …).
- Failing checks → verify the target independently, then record the result.

## Escalation path

NOC → SRE → Platform team. Major incidents require a commander; recovery ends
in RESOLVED → postmortem (POSTMORTEM_REQUIRED) → action items → CLOSED.
