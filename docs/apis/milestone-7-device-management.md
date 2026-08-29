# Milestone 7 — Device Management Service: CPE Control Plane API

Service: `device-management-service`. Auth: management JWT (`DEVICE_MANAGEMENT_JWT_SECRET`) with RBAC for all `/api/device-management/*` routes; internal-service key (`X-Internal-API-Key`) for inbound cross-service ingestion. All routes are tenant-scoped (`tenant_id` query parameter is validated against the authenticated principal).

This service is a **business-facing device-management control plane** for TR-069 CPE. It never exposes the GenieACS NBI to frontends; all CWMP/parameter-tree/RPC work is delegated to the separately deployed GenieACS ACS (see the [GenieACS configuration guide](../genieacs-configuration-guide.md)).

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

## Device onboarding (identity, tenant resolution, claiming)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/devices/discover` | Pull a device from the ACS, normalize identity (OUI/product-class/serial), create/update the managed CPE. Unknown devices are quarantined. Publishes `cpe.discovered.v1` |
| POST | `/api/device-management/devices/{device_id}/resolve-tenant` | Explicit tenant resolution (PREREGISTERED_SERIAL, ADMIN_CLAIM, OSS_ORDER_RESERVATION, …) |
| POST | `/api/device-management/devices/{device_id}/claim` | Claim a device after valid ownership (pre-registered serial evidence, cross-tenant claims blocked) |
| POST | `/api/device-management/devices/{device_id}/assign` | Link business entities (customer, subscription, service location, OSS order, work order, inventory) |
| POST | `/api/device-management/devices/{device_id}/transfer` | Transfer ownership between tenants (requires reason; preserves ownership history) |
| POST | `/api/device-management/devices/{device_id}/decommission` | Decommission a device (end-of-life); publishes `cpe.decommissioned.v1` |
| GET | `/api/device-management/devices` | List managed devices (tenant-scoped, filterable) |
| GET | `/api/device-management/devices/{device_id}` | Device detail + immutable timeline |
| POST | `/api/device-management/devices/{device_id}/refresh` | Force a parameter refresh from the ACS |

## Device-model catalogue & vendor-neutral profiles

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/profiles` | Create a versioned configuration profile |
| POST | `/api/device-management/profiles/{profile_id}/versions` | Create an immutable version (sensitive values stored encrypted as references) |
| POST | `/api/device-management/profiles/versions/{version_id}/submit` | Submit a draft for review |
| POST | `/api/device-management/profiles/versions/{version_id}/approve` | Approve a version |
| POST | `/api/device-management/profiles/versions/{version_id}/activate` | Activate (supersedes prior versions) |
| POST | `/api/device-management/profiles/versions/{version_id}/compile-preview` | Compile a vendor-neutral profile to TR-098/TR-181 paths; report unsupported params |
| POST | `/api/device-management/profiles/{profile_id}/assignment-rules` | Add an explainable assignment rule |
| GET | `/api/device-management/devices/{device_id}/profile-decision` | Resolve the profile for a device (rule match + explainable decision) |

## Configuration jobs (apply + read-back verification)

A queued task is **never** treated as success: the job only succeeds after
read-back verification confirms the device applied the parameters.

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/devices/{device_id}/configuration-jobs` | Create a configuration job (profile or raw parameters; idempotent) |
| GET | `/api/device-management/configuration-jobs/{job_id}` | Job detail + verification state |
| POST | `/api/device-management/configuration-jobs/{job_id}/approve` | Approve (validation gate) |
| POST | `/api/device-management/configuration-jobs/{job_id}/queue` | Queue (records timeout) |
| POST | `/api/device-management/configuration-jobs/{job_id}/execute` | Dispatch to GenieACS (durable task + connection request); offline devices wait for Inform |
| POST | `/api/device-management/configuration-jobs/{job_id}/task-result` | ACS task result callback (COMPLETED/FAULTED) |
| POST | `/api/device-management/configuration-jobs/{job_id}/verify` | Read back parameters and compare with desired state (sensitive params exempt) |
| POST | `/api/device-management/configuration-jobs/{job_id}/cancel` | Cancel a non-terminal job |
| POST | `/api/device-management/devices/{device_id}/observed` | Record observed device state (from ACS/Inform) |
| POST | `/api/device-management/devices/{device_id}/detect-drift` | Compare desired vs observed; classify drift; publishes `cpe.configuration_drift_detected.v1` |
| GET | `/api/device-management/devices/{device_id}/drift` | Drift history for a device |

## Controlled device actions

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/devices/{device_id}/actions` | Create a controlled action (REBOOT, FACTORY_RESET, CONNECTION_REQUEST, …); elevated actions require elevated permission |
| GET | `/api/device-management/actions/{action_id}` | Action detail |
| POST | `/api/device-management/actions/{action_id}/approve` | Approve a pending action |
| POST | `/api/device-management/actions/{action_id}/execute` | Execute via GenieACS |
| POST | `/api/device-management/actions/{action_id}/outcome` | Record outcome (connection-request URL is SSRF-validated) |

## Capability-aware diagnostics

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/device-management/devices/{device_id}/diagnostics/supported` | Diagnostics supported by the device model/variant |
| POST | `/api/device-management/devices/{device_id}/diagnostics` | Create a diagnostic job (PING, TRACEROUTE, …); unsupported flagged |
| GET | `/api/device-management/diagnostics/{job_id}` | Diagnostic job detail |
| POST | `/api/device-management/diagnostics/{job_id}/run` | Dispatch (offline → waits, not failed) |
| POST | `/api/device-management/diagnostics/{job_id}/result` | Submit result (evaluated, normalized); supports offline/fault |

## Firmware repository & rollouts

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/firmware` | Upload firmware (checksum validated; duplicate vendor/model/version rejected) |
| POST | `/api/device-management/firmware/{artifact_id}/approve` | Approve/reject an artifact |
| POST | `/api/device-management/firmware/{artifact_id}/compatibility` | Declare model-variant compatibility |
| POST | `/api/device-management/firmware/rollouts` | Create a rollout (CANARY/PHASED/LAB); requires approved artifact |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/stages` | Build/approve rollout stages |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/start` | Start the rollout (stage 1 canary first) |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/deployments` | Queue a device deployment (compatibility + rollback-capability aware) |
| POST | `/api/device-management/firmware/deployments/{deployment_id}/execute` | Execute the firmware download/upgrade via GenieACS |
| POST | `/api/device-management/firmware/deployments/{deployment_id}/outcome` | Report post-upgrade outcome (reported version verified; rollback only if supported) |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/advance` | Evaluate stage outcomes; pause/advance/complete by thresholds |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/pause` | Pause a rollout (manual) |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/resume` | Resume a paused rollout |
| POST | `/api/device-management/firmware/rollouts/{rollout_id}/stop` | Stop a rollout |

## ACS instances & health

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/acs/instances` | Register a GenieACS instance (credentials encrypted) |
| POST | `/api/device-management/acs/instances/{instance_id}/health-check` | Health check the ACS (circuit-breaker aware) |
| GET | `/api/device-management/acs/instances` | List ACS instances |
| POST | `/api/device-management/acs/instances/{instance_id}/reconcile` | Reconcile managed CPEs against the ACS device list |

## Telemetry & NMS signal

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/device-management/devices/{device_id}/telemetry` | Record telemetry signal (rate-limited) |
| POST | `/api/device-management/devices/{device_id}/nms-signal` | Accept NMS investigation signal (internal) |

## Reports & audit

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/device-management/reports/overview` | Fleet overview counts by state/compliance |
| GET | `/api/device-management/reports/devices` | Device report (by state, manufacturer, model) |
| GET | `/api/device-management/audit` | Audit log (tenant-scoped) |

## Events

Published (`cpe.*`): discovered, claimed, online, offline, assigned,
configuration_requested, configuration_applied, configuration_failed,
configuration_drift_detected, diagnostic_completed, firmware_upgrade_started,
firmware_upgrade_completed, firmware_upgrade_failed, rebooted,
replacement_required, decommissioned.

Consumed (idempotent): `inventory.device_reserved.v1`,
`inventory.device_installed.v1`, `inventory.device_recovered.v1`,
`work_order.device_installed.v1`, `order.cpe_provisioning_requested.v1`,
`service.activated.v1`, `service.plan_changed.v1`,
`ticket.device_diagnostic_requested.v1`, `nms.device_investigation_requested.v1`.
