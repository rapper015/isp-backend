# Assurance Security Checklist

Security controls implemented by the assurance-service (Milestone 9) and the
observability stack. Verify each item before promoting to production.

## Authentication & authorization

- [ ] Management JWT signed with `ASSURANCE_JWT_SECRET` (**≥32 chars**) —
      shorter secrets return 503.
- [ ] RBAC by role (`ROLE_PERMISSIONS`) with per-endpoint permission mapping.
- [ ] **Elevated permissions** for sensitive operations: incident close/declare,
      SLO approve/activate, maintenance approve, postmortem approve, root-cause
      confirm, platform aggregates.
- [ ] Internal ingest (`X-Internal-API-Key`) only for `/internal/assurance/*`;
      the edge gateway does **not** proxy internal paths.
- [ ] Rate limiting on management endpoints (Redis; fail-open when Redis absent).

## Tenant isolation

- [ ] Tenant-owned data fails **closed** without a validated `TenantContext`.
- [ ] Cross-tenant reads/writes rejected (`TenantIsolationError` → 403).
- [ ] Platform aggregates (`/dashboards/platform`, `/reports/aggregate`) require
      explicit `scope_kind: PLATFORM_AGGREGATE`.

## Telemetry / secrets

- [ ] Raw telemetry is **never** stored in the Python service — it lives in
      Prometheus/Loki/Tempo via the OTel Collector.
- [ ] OTel Collector `attributes/redact` processor strips customer_id,
      subscriber_id, username, order_id, ip_address, mac_address, password,
      authorization from exported signals.
- [ ] Structured JSON logging uses `RedactionFilter` (passwords, tokens, API
      keys, secrets redacted).
- [ ] Metric/alert **cardinality policy**: labels restricted to `SAFE_LABELS`;
      high-cardinality/sensitive values rejected at ingest.

## Incident / root-cause integrity

- [ ] Incident state machine validated; RESOLVED only via MONITORING.
- [ ] Support tickets are **linked, never conflated** with incidents.
- [ ] Estimated vs confirmed customer impact kept separate.
- [ ] Root cause confirmation requires ≥1 supporting evidence, no contradicting
      evidence, and explicit human confirmation. **Temporal coincidence is never
      auto-confirmed.**
- [ ] Postmortem action items cannot be deleted.

## Auditability

- [ ] Append-only `AuditLog` for governance actions (approve/activate/confirm/
      postmortem/maintenance) with actor + correlation_id.
- [ ] Notification delivery records (`NotificationDelivery`) for routing audit.
- [ ] All events carry `correlation_id`, `idempotency_key` and W3C `trace_context`.

## Infrastructure

- [ ] Prometheus/Alertmanager/Grafana/Loki/Tempo/OTel Collector run in the
      `observability` profile only.
- [ ] `memory_limiter` (512 MiB) + `batch` on the Collector; Prometheus 15d
      retention; Tempo 48h.
- [ ] Observability endpoints (Prometheus 9090, Grafana 3000) are **not**
      exposed on the edge gateway; internal platform network only.

## Remaining (external) requirements

- [ ] TLS termination + auth in front of Grafana if exposed externally.
- [ ] Rotate `ASSURANCE_JWT_SECRET` and `ASSURANCE_INTERNAL_API_KEY` regularly.
- [ ] Postgres exporter / RabbitMQ exporter / Valkey exporter containers
      configured and scraped (targets referenced in `prometheus.yml`).
