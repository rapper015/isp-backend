# Firmware Operations Runbook — Device Management (Milestone 7)

Safe firmware rollout requires **approval before distribution, canary staging
first, read-back verification per device, and honest rollback claims**. This
runbook covers the control-plane flow; GenieACS performs the actual CWMP
download.

## 1. Roles

| Role | Permission |
| --- | --- |
| FIRMWARE_OPERATOR | `device.firmware.upload`, `device.firmware.rollout`, `device.firmware.execute` |
| FIRMWARE_APPROVER | `device.firmware.approve`, `device.firmware.rollout`, `device.firmware.approve_stage` |
| ISP_ADMIN / PLATFORM_ADMIN | full firmware control |

## 2. Upload & approval

```http
POST /api/device-management/firmware
{ "vendor": "FiberHome", "model": "AN5506-04-F1", "version": "V2.0",
  "checksum_sha256": "<sha256 of the file>" }
```

- The checksum is validated against the file content. A mismatched checksum is
  rejected.
- A duplicate `vendor/model/version/file_type` is rejected.
- Artifacts start `UPLOADED` and **cannot** be rolled out until approved:

```http
POST /api/device-management/firmware/<artifact_id>/approve
{ "decision": "APPROVED", "reviewed_by": "fw-approver" }
```

Declare compatibility for model variants:

```http
POST /api/device-management/firmware/<artifact_id>/compatibility
{ "model_variant_id": "<variant>", "min_current_version": "V1.0", "verified": true }
```

## 3. Create & start a rollout

```http
POST /api/device-management/firmware/rollouts
{ "artifact_id": "<id>", "name": "canary-v2", "strategy": "CANARY",
  "policy": { "stage_percentages": [1, 5, 10, 25, 59],
              "success_threshold": 0.95, "failure_threshold": 0.1,
              "observation_period_minutes": 30 } }

POST /api/device-management/firmware/rollouts/<rollout_id>/stages
POST /api/device-management/firmware/rollouts/<rollout_id>/start
```

- `create_rollout` requires an **approved** artifact.
- Stage 1 is the canary (default `requires_manual_approval=true`); the fleet is
  never targeted in full as the first stage.

## 4. Deploy to a device

```http
POST /api/device-management/firmware/rollouts/<rollout_id>/deployments
{ "cpe_id": "<device>", "stage_id": "<stage>" }

POST /api/device-management/firmware/deployments/<deployment_id>/execute
```

- The deployment is compatibility-checked against the device's model variant.
- The download task is durable in GenieACS; the connection-request outcome is
  recorded.

## 5. Outcome & verification

```http
POST /api/device-management/firmware/deployments/<deployment_id>/outcome
{ "reported_firmware": "V2.0", "health_checks": { "ping": true } }
```

- The reported firmware version is compared to the artifact version.
- Success → `SUCCEEDED`, device firmware updated, `COMPLIANT`,
  `cpe.firmware_upgrade_completed.v1` published.
- Mismatch → `FAILED`, `cpe.firmware_upgrade_failed.v1` published; rollback is
  only claimed when the variant supports it (`DUAL_BANK`/`VENDOR_DOWNGRADE`/
  `AUTOMATIC_BOOT_ROLLBACK`).

## 6. Advance / pause / stop

```http
POST /api/device-management/firmware/rollouts/<rollout_id>/advance   # evaluate stage thresholds
POST /api/device-management/firmware/rollouts/<rollout_id>/pause     # manual hold
POST /api/device-management/firmware/rollouts/<rollout_id>/resume
POST /api/device-management/firmware/rollouts/<rollout_id>/stop
```

`advance` promotes the earliest pending stage to `RUNNING` (if none is running)
and evaluates the running stage:

- failure ratio ≥ `failure_threshold` → **PAUSE** (rollout halts, operator
  reviews);
- success ratio ≥ `success_threshold` (and minimum sample met) → stage
  **SUCCEEDED**, next stage promoted;
- otherwise **CONTINUE** (keep collecting outcomes).

## 7. Incident response

| Symptom | Action |
| --- | --- |
| Stage failure ratio exceeded | Rollout auto-pauses; review `advance` response; decide resume/stop |
| A device reports the old firmware after "success" | Verification marks it FAILED — do not accept the task-complete as proof |
| Rollback needed but hardware lacks dual-bank | Do NOT claim rollback; treat as failed/quarantined and dispatch a field swap |
| Wrong artifact uploaded | Reject via `approve` decision; checksum mismatch already blocks bad uploads |

## 8. Safety invariants

- No fleet-wide first stage.
- No success without per-device version read-back.
- No rollback claim without hardware support.
- No rollout of an unapproved artifact.
