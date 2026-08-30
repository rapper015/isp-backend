# SLO Authoring Guide

How to define, validate, publish and operate SLIs/SLOs on the platform.

## SLI first

An SLO is meaningless without a good SLI. Define an SLI per service:

```json
{
  "code": "sli_radius_auth_success",
  "name": "RADIUS auth success rate",
  "service_id": "<service id>",
  "measurement_source": "radius",
  "good_event_definition": "Access-Accept",
  "valid_event_definition": "Access-Request",
  "unit": "ratio"
}
```

Good events are a subset of valid events; everything outside `valid` is
excluded from the SLI (not counted as bad).

## Create the SLO

```json
{
  "code": "slo_radius_auth",
  "sli_id": "<sli id>",
  "objective": 0.999,
  "window_type": "ROLLING",
  "window_seconds": 2592000,
  "service_tier": "TIER_1",
  "published": true
}
```

## Lifecycle

```
DRAFT → VALIDATING → APPROVED → ACTIVE → SUPERSEDED / DISABLED → ARCHIVED
```

- `published: true` creates an **immutable version**. Published versions are
  never edited; changes publish a **new version** (`SloVersion`).
- Approval and activation are elevated operations (SRE / PLATFORM_ADMIN).

## Error budget & burn rate

The worker computes `SloWindowState` for the active window from the recorded
measurements, the objective, the window type/size and the **policy version** —
reproducible by construction.

- `sli_ratio = good / total`
- `allowed_bad = (1 - objective) * total`
- `remaining_budget = (allowed_bad - consumed_bad) / allowed_bad`
- `burn_rate = consumed_bad / allowed_bad`
- Status: HEALTHY → WARNING → AT_RISK → BREACHED → EXHAUSTED
- **Fast burn**: `burn_rate ≥ 14.4` (budget exhausted in < 5 days of 30d).
- **Slow burn**: `1.0 ≤ burn_rate < 14.4`.

## Maintenance windows

Approved maintenance windows exclude their events from the **contractual**
window (via `MaintenanceException`). Raw measurements are always preserved.
`GET /api/assurance/v1/slos/{id}/error-budget` reflects exclusions.

## Measurement recording

Record measurements as domain events arrive (consumers map them idempotently)
or via `POST /sli-measurements`:

```json
{"sli_code": "sli_radius_auth_success", "good": 998, "total": 1000}
```

For a given window the totals are summed; quality flags mark invalid/missing
data. Do **not** pre-aggregate on the service side — record raw counts.

## Guidance

- Prefer 30-day rolling windows for availability; use CALENDAR for monthly
  business SLAs.
- Alert on burn rate, not just on ratio: `SloFastBurn` (CRITICAL) and
  `SloSlowBurn` (MEDIUM) alert rules are shipped in
  `infrastructure/observability/alert-rules/platform.yml`.
- Every SLO needs an owner and, once ACTIVE, a review cadence.
