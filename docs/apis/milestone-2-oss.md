# Milestone 2 — Complete Frontend API Handoff: OSS

Service: `oss-service`. Auth: management JWT (`Authorization: Bearer`) with OSS
RBAC (`management_auth`); all `/api/oss/*` routes are tenant-scoped.

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

## Orders

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/oss/orders` | Create an order (event-sourced, DRAFT) |
| GET | `/api/oss/orders` | List orders (filter by tenant/type/state) |
| GET | `/api/oss/orders/{order_id}` | Order detail |
| POST | `/api/oss/orders/{order_id}/submit` | Submit order → SUBMITTED |
| POST | `/api/oss/orders/{order_id}/validate` | Run validation phase (→ READY / PAYMENT_PENDING / VALIDATION_FAILED) |
| POST | `/api/oss/orders/{order_id}/approve-payment` | Approve payment → READY_FOR_FULFILMENT |
| POST | `/api/oss/orders/{order_id}/fulfil` | Start the provisioning workflow (saga) |
| POST | `/api/oss/orders/{order_id}/cancel` | Request cancellation (or cancel DRAFT) |
| POST | `/api/oss/orders/{order_id}/retry` | Retry a failed/validation-failed order |
| POST | `/api/oss/orders/{order_id}/resume` | Resume a manual-intervention order |
| POST | `/api/oss/orders/{order_id}/compensate` | Trigger compensation |
| GET | `/api/oss/orders/{order_id}/valid-actions` | Valid next transitions for the state |
| GET | `/api/oss/orders/{order_id}/events` | Immutable event stream (aggregate replay) |
| GET | `/api/oss/orders/{order_id}/history` | Status history |

## Workflows (sagas)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/oss/orders/{order_id}/workflows` | Sagas for an order |
| GET | `/api/oss/workflows/{saga_id}` | Saga detail |
| GET | `/api/oss/workflows/{saga_id}/steps` | Saga steps + attempts |
| POST | `/api/oss/workflows/{saga_id}/resume` | Resume a saga |

## Resources

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/oss/resources/register` | Register a resource in the inventory |
| GET | `/api/oss/resources/capacity` | Capacity counts by type/status |
| GET | `/api/oss/resources/reservations` | Reservation ledger (tenant/order filter) |

## Subscriptions

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/oss/subscriptions` | List service subscriptions |
| GET | `/api/oss/subscriptions/{subscription_id}` | Subscription detail |

## Manual interventions

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/oss/manual-interventions` | Open manual interventions |
| POST | `/api/oss/manual-interventions/{intervention_id}/resolve` | Resolve + resume the workflow |

## Frontend integration handoff

Public base is `/api/oss/` (or `/api/v1/oss/`) and every request uses the
management bearer token plus tenant scope. Build the order page from order
detail, `valid-actions`, history, events, and workflows; do not derive enabled
actions from a client-side state enum. Create orders with one idempotency key per
user click, then keep the returned `id` and `order_number`.

Normal flow: DRAFT → submit → validate → READY or PAYMENT_PENDING →
approve-payment → READY_FOR_FULFILMENT → fulfil. Fulfilment starts a saga: poll
workflow detail/steps until terminal state and do not show installation success
until the order, saga, and subscription confirm it. Retry, resume, compensate,
and manual intervention resolution are only shown if the backend reports the
action as valid. Preserve the reason/actor/idempotency fields when retrying the
same intent. See the shared frontend contract for token refresh and errors.
