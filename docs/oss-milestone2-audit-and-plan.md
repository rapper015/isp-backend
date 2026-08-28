# OSS Milestone 2 — Audit and Implementation Plan

## 1. Repository audit summary

The platform is a **FastAPI + SQLAlchemy microservice monorepo** (not the Django
monolith the brief assumed). The Docker image is shared
(`infrastructure/service.Dockerfile`) and each service owns its own PostgreSQL
database, RabbitMQ access, Redis/Valkey access and a gateway route.

| Service | State at start of Milestone 2 |
| --- | --- |
| `aaa-service` | Full: RADIUS auth/authorization/accounting, sessions, CoA/Disconnect, encrypted credentials, NAS/MikroTik orchestration (Milestone 0). |
| `crm-service` | Milestone 1 complete: leads, customers, contacts, addresses, KYC, CAF, lifecycle, risk, timeline, audit (committed on `milestone-1`). |
| `oss-service` | Minimal foundation: `Order` and `Subscriber` models with basic CRUD, **no** event sourcing, no state machine, no saga, no resource reservation. |
| `ipam-service` | Minimal foundation: `IPPool`/`IPAddress` with a simple allocate endpoint. |
| Other services | Foundations (nms, siem, workforce, warehouse, aiops, bss). |

## 2. Component classification

| Component | Decision |
| --- | --- |
| `oss-service` `Order`/`Subscriber` models | **REFACTOR → MIGRATE** into event-sourced `Order` aggregate + `ServiceSubscription`. |
| `oss-service` basic CRUD | **REPLACE** with domain commands + validated state machine + event stream. |
| `ipam-service` allocate | **EXTEND (adapter)** — OSS coordinates reservations; IPAM adapter is the integration point. |
| AAA/NAS integrations | **KEEP/REUSE** through adapters (`aaa_client`, `nas_client`); no FreeRADIUS changes. |

## 3. Milestone 2 scope implemented in `oss-service`

1. **Event-sourced orders**: `Order`, `OrderEvent` (immutable, append-only,
   aggregate version, optimistic concurrency), `OrderStatusHistory`,
   `OrderCommand` deduplication, outbox/inbox.
2. **Order state machine**: validated `DRAFT → … → COMPLETED/ROLLED_BACK/CANCELLED`
   with `MANUAL_INTERVENTION_REQUIRED`.
3. **Resource reservation**: deterministic, conflict-free ledger
   (`AVAILABLE → RESERVED → ALLOCATED → RELEASED`, TTL, quarantine) with
   database uniqueness + Redis assist; adapter interface to IPAM/Network
   inventory.
4. **Saga orchestration**: durable `SagaInstance`/`SagaStep`/`SagaStepAttempt`
   engine with retries, timeouts, compensation, resume and manual intervention.
5. **Zero-touch activation** (`NEW_CONNECTION` saga) with adapters to
   CRM/BSS/IPAM/Network/Workforce/AAA/NAS/NMS — deterministic fakes for tests.
6. **Additional workflows**: upgrade/downgrade/suspension/reactivation/
   termination as order types reusing the saga engine.
7. **RabbitMQ outbox/inbox** with versioned `oss.*` and `resource.*` events.
8. **Redis** for reservation acceleration, idempotency and worker coordination
   (never the source of truth).
9. **APIs** under `/api/oss/...`, **tests**, **migrations**, **docs**.

## 4. Boundary enforcement

- OSS owns orders, workflow/saga state, resource-reservation coordination and
  the operational service lifecycle.
- CRM owns customer identity/lifecycle; BSS owns billing; AAA owns subscriber
  access; IPAM/Network inventory own resource ownership; NMS verifies; Workforce
  installs. OSS talks to them through adapters + versioned events only.
- No cross-service database access. No FreeRADIUS or NAS configuration files are
  modified; NAS changes go through the existing AAA integration adapter.
