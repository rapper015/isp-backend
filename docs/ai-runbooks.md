# AI Operations Runbooks — Milestone 10

Runbooks for deploying, monitoring, rolling back, and responding to incidents
in the intelligence layer. The AI never acts outside its governed boundaries.

## 1. Model deployment & promotion

1. Build a **dataset snapshot**: `POST /api/intelligence/v1/datasets`
   (contracts filter, criteria). Approve it:
   `POST /datasets/{id}/approve` (elevated).
2. **Train + register**: `POST /training` (algorithm, feature_names, parameters,
   threshold, scope). The run performs a time-based split + leakage check and
   writes evaluation metrics (precision/recall/PR-AUC/ROC-AUC/ECE + baseline
   lift) and a model card.
3. **Approve**: `POST /models/{id}/approve` (MLOPS_ENGINEER / PLATFORM_ADMIN).
   Never approve on accuracy alone — compare PR-AUC and recall at the operating
   threshold against the baseline; check performance by tenant/segment.
4. **Deploy**: `POST /models/{id}/deploy` with `environment`:
   - `SHADOW` — score in parallel, no action.
   - `CANARY` — limited traffic.
   - `PRODUCTION` — full.
5. **Monitor** (`GET /monitoring`): prediction distribution, drift, latency,
   error rate, completeness. `POST /models/{id}/drift` alerts when deviation
   exceeds threshold.

## 2. Rollback

- `POST /models/{id}/rollback` (elevated). The registry records the previous
  version as `rollback_target`; the current deployment is ended and the model
  moves to `ROLLED_BACK`. Re-promote the prior version by deploying it again
  (approve → shadow → canary → production).
- **Retire**: `POST /models/{id}/retire` to archive superseded versions.

## 3. Drift & degradation response

- Drift alert (`ai.model_drift_detected.v1` / `ModelMonitor.alert=true`) means
  the serving distribution deviates from training. Actions:
  1. Check feature freshness (`GET /features/values`, quality markers).
  2. Check pipeline health (`GET /quality`, `GET /raw-events`).
  3. If confirmed, roll back and retrain on a fresh snapshot.
- Stale features: worker marks `OnlineFeatureValue.quality=STALE`; predictions
  on stale features should be treated as low-confidence.

## 4. Remediation safety & kill switch

- Every automated action is a **RemediationIntent** (idempotent). L2+ requires
  approval; L3 is pre-approved only for low-impact reversible actions.
- **Kill switch**: `POST /api/intelligence/v1/kill-switch` (scope GLOBAL or
  TENANT, elevated). Engaged → new intents and executions are blocked
  immediately (`ai.kill_switch_engaged.v1` published).
- **Budget/cooldown/circuit breaker**: a failing policy opens its circuit
  breaker after N failures in the window; retries stop until reset.

## 5. Incident response (AI-related)

1. If an automated action is misbehaving: engage the tenant or global kill
   switch first.
2. Compensate/rollback any started intents (`/remediation/intents/{id}/fail`
   with `compensate=true`).
3. Audit: `GET /audit-log` shows every request/approval/execution with actor +
   correlation_id.
4. Trace the intent chain: `signal → prediction → recommendation → approval →
   domain action → verification → outcome` via correlation/causation ids.
5. Open a support incident (owned by assurance/CRM) linking the intent ids.

## 6. Data pipeline backfill / replay

- `POST /replay` reprocesses raw events into analytical records idempotently
  (no duplicates). Use for backfills after schema or transform changes.
- Quarantined records (`state=QUARANTINED`) are visible in `/raw-events`;
  fix the contract or the payload, then re-ingest with a new event id.

## 7. Production integrations (external, not auto-deployed)

The intelligence layer does **not** deploy or administer FreeRADIUS, RouterOS,
GenieACS, CPE, payment gateways or network infrastructure. Domain actions
required by a recommendation must be executed by the owning service through its
own audited APIs and sagas.
