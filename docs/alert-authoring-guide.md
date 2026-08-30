# Alert Authoring Guide

Rules for creating maintainable, noise-free alerts on the platform.

## Alert definition fields

Every alert rule carries: `service`, `alert_name`, `component`, `resource`,
`severity`, `impact` (customer impact statement), `owner`, `routing_labels`,
`runbook_url`, `duration_seconds`, `condition`, `auto_resolution_rule`, and a
**test status** (`AlertDefinitionTest`).

## Severity

| Severity | Meaning | Action |
| --- | --- | --- |
| CRITICAL | service down / SLA at risk / security | page immediately |
| HIGH | degraded customer path | page, 15 min |
| MEDIUM | degraded non-customer path | queue, investigate |
| LOW | operational hygiene | queue |
| INFORMATIONAL | context only | no page |

LOW with customer impact is elevated to MEDIUM (impact-aware severity).

## Fingerprints & dedup

- Fingerprints are **stable**: `service|alert_name|resource|component|tenant`.
  Never include timestamps, instance ids or random values in a fingerprint.
- Repeated events within the dedup window increment `firing_count` instead of
  creating new alerts.

## Labels & cardinality policy

Metric/alert **labels are restricted** to approved dimensions:
`service, environment, region, tenant_tier, operation, result, error_class,
device_model, access_technology, severity, component, resource, pop, provider`.

**Never** use these as labels: customer IDs, subscriber IDs, usernames, ticket
ids, order UUIDs, trace/session ids, invoice ids, serial numbers, IPs, MACs,
passwords/tokens/API keys. High-cardinality or sensitive values are **rejected**
at ingest (fail loudly in dev, drop in prod).

Put high-cardinality identifiers in the **payload/evidence** (logs/traces), not
in labels.

## Grouping, inhibition, silencing

- **Grouping**: `service | component | resource | pop`.
- **Inhibition**: a FIRING/ACKNOWLEDGED parent on the same component suppresses
  downstream alerts (e.g. POP router down suppresses CPE-offline alerts).
- **Silencing**: use for planned changes with a start/end window and a reason.

## Flapping

Frequent FIRING/RESOLVED oscillation (≥4 states, ≥max(3, len/2) transitions) is
detected and the alert is auto-suppressed. Fix the root cause rather than
silencing.

## Routing

Routes match labels with fallback: each alert is routed to the most specific
matching route; unmatched alerts go to the `DEFAULT` fallback (STATUS_PAGE).
Notification deliveries are recorded (`NotificationDelivery`) for audit.

## Tests

Every alert definition should have a scenario test
(`AlertDefinitionTest`): input → expected state. Test before activating.

## Runbooks

Every alert must reference a runbook URL (this repo: `docs/noc-runbook.md`,
`docs/incident-response-runbook.md`).
