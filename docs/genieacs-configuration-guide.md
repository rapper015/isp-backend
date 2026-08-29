# GenieACS Configuration Guide — Device Management (Milestone 7)

The `device-management-service` is a **business-facing control plane** that
drives TR-069 CPE through a **separately deployed GenieACS** ACS. This service
does **not** implement the CWMP protocol and does **not** touch GenieACS's
MongoDB. All protocol sessions, parameter-tree storage, RPC execution and
pending tasks remain owned by GenieACS; this service talks to the GenieACS
HTTP NBI only through the `app/integrations/acs.py` adapter.

## 1. Topology

```mermaid
graph LR
    CPE[TR-069 CPE] -->|CWMP / Inform / Connection Request| GA[GenieACS ACS]
    GA -->|HTTP NBI (internal)| DM[device-management-service]
    DM -->|business control plane| OPS[ISP Ops / NOC / Firmware tools]
    DM -->|RabbitMQ events| BUS[(Event bus)]
    GA -->|MongoDB (never touched by DM)| DB[(Mongo)]
```

- GenieACS is deployed as a standalone service (own container/compose service),
  listening on `:7547` (CWMP) and `:7557` (HTTP NBI).
- The device-management service registers the ACS as an **ACS instance**
  (`POST /api/device-management/acs/instances`) and routes all device
  operations through the adapter for that instance.
- The GenieACS NBI is **never exposed** to frontends; only the
  device-management service talks to it, over an internal network.

## 2. GenieACS deployment

Deploy GenieACS (e.g. via its Docker image) with:

```yaml
services:
  genieacs:
    image: genieacs/genieacs:latest
    environment:
      GENIEACS_CWMP_ACCESS: "0.0.0.0:7547"
      GENIEACS_NBI_ACCESS: "0.0.0.0:7557"
      GENIEACS_MONGO_URI: "mongodb://mongo:27017/genieacs"
      GENIEACS_FS_ACCESS: "0.0.0.0:7567"
    ports:
      - "7547:7547"
      - "7557:7557"
    depends_on:
      mongo:
        condition: service_healthy
    networks:
      - platform   # same internal network as device-management-service
```

> **Security**: bind the NBI to the internal Docker network only. Do not
> publish `7557` to the internet. Restrict the MongoDB port to the internal
> network.

## 3. Device-management service configuration

`.env.example` for the service:

```ini
# ACS provider: "fake" (hermetic tests/dev) or "genieacs" (production)
ACS_PROVIDER=genieacs
GENIEACS_BASE_URL=http://genieacs:7557
GENIEACS_TIMEOUT=10
GENIEACS_MAX_RETRIES=3

# Management JWT + internal key
DEVICE_MANAGEMENT_JWT_SECRET=<>=32-char secret
DEVICE_MANAGEMENT_INTERNAL_API_KEY=<shared internal key>
DEVICE_MANAGEMENT_ENCRYPTION_KEY=<Fernet-key-material>

DEVICE_FIRMWARE_DIR=/data/firmware
DEVICE_MGMT_WORKER_INTERVAL=30
DEVICE_MGMT_JOB_TIMEOUT_MINUTES=30
```

### What the adapter does (and does not do)

| Concern | Handled by GenieACS | Handled by device-management |
| --- | --- | --- |
| CWMP Inform handling | ✅ | ❌ |
| Parameter tree storage | ✅ (MongoDB) | ❌ |
| RPC / task execution | ✅ | ❌ |
| Connection requests | ✅ (outbound to CPE) | ❌ (only triggers + validates the URL) |
| Business identity (tenant/CPE) | ❌ | ✅ |
| Device-model catalogue + versioned profiles | ❌ | ✅ |
| Configuration jobs + read-back verification | ❌ | ✅ |
| Drift / reconciliation | ❌ | ✅ |
| Firmware approval + canary rollouts | ❌ | ✅ |
| RBAC / audit | ❌ | ✅ |

## 4. Registering an ACS instance

```http
POST /api/device-management/acs/instances
Authorization: Bearer <management-jwt>
{
  "name": "genieacs-prod-1",
  "base_url": "http://genieacs:7557",
  "environment": "PRODUCTION"
}
```

Health check:

```http
POST /api/device-management/acs/instances/<instance_id>/health-check
```

The adapter uses a circuit breaker: after repeated failures the instance is
marked unhealthy and calls fail fast until the cooldown expires.

## 5. Connection requests and the SSRF boundary

When the platform triggers a connection request it may supply a URL. The
service validates the URL (scheme, port, IP class, DNS resolution) and rejects
link-local/metadata/lan targets (`169.254.169.254`, `10/8`, `192.168/16`, …)
before any outbound call — see `app/domain/ssrf.py`.

## 6. Operational notes

- The worker (`python -m app.worker_runner`) polls in-flight configuration
  jobs and diagnostics, times out stale jobs, reconciles drift, advances
  firmware rollouts and syncs the ACS device list. Run it as a separate
  container (`device-management-worker`).
- GenieACS device discovery happens on `discover`/`reconcile`; the service
  never scrapes the whole ACS without an explicit reconciliation call.
- GenieACS presets/provisions/virtual-parameters/files are managed through the
  adapter (`manage_presets`, `manage_provisions`, `manage_virtual_parameters`,
  `upload_file`) — the control plane can stage them, but the CWMP execution
  still happens on the GenieACS side.
