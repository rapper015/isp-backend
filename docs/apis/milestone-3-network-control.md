# Milestone 3 — Network Control API (mounted on `aaa-service`)

Service: `aaa-service` (`/api/aaa/...`, network-control router). Auth:
`X-AAA-Service-Key` (internal) with management JWT RBAC fallback; all routes are
tenant-scoped. Includes the Milestone 0 AAA core plus the network-control plane
below.

## Policies

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/policies` | Create a vendor-neutral policy (+ version 1, DRAFT) |
| GET | `/api/aaa/policies` | List policies |
| POST | `/api/aaa/policies/{policy_id}/versions` | Create a new version (immutable published bodies) |
| GET | `/api/aaa/policies/{policy_id}/versions/{version}` | Policy version detail |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/validate` | Validate policy body |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/preview` | Preview compiled RADIUS attributes |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/submit` | DRAFT → UNDER_REVIEW |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/approve` | UNDER_REVIEW → APPROVED |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/schedule` | Schedule activation (effective_from) |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/activate` | Activate (supersedes other active versions) |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/disable` | Disable version |
| POST | `/api/aaa/policies/{policy_id}/versions/{version}/rollback` | Roll back to a previous version |
| POST | `/api/aaa/subscribers/{subscriber_id}/policy-assignment` | Assign a policy version to a subscriber |
| POST | `/api/aaa/subscribers/{subscriber_id}/overrides` | Add a temporary override |
| DELETE | `/api/aaa/subscribers/{subscriber_id}/overrides/{override_id}` | Remove override |
| POST | `/api/aaa/subscribers/{subscriber_id}/effective-policy/explain` | Evaluate + persist an explainable decision |

## Sessions

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/network/sessions` | List active sessions (status/NAS filters) |
| GET | `/api/aaa/network/sessions/search` | Search by username/IP/MAC/session |
| GET | `/api/aaa/network/sessions/{session_id}` | Session detail |
| GET | `/api/aaa/network/sessions/{session_id}/timeline` | Session timeline projection |
| POST | `/api/aaa/network/sessions/{session_id}/disconnect` | Precise session disconnect (control action) |
| POST | `/api/aaa/network/subscribers/{subscriber_id}/disconnect-all` | Bulk disconnect (requires approval) |
| POST | `/api/aaa/network/sessions/{session_id}/reapply` | Reapply current policy via CoA |
| POST | `/api/aaa/network/sessions/{session_id}/force-reauth` | Disconnect + re-authenticate (IP/pool changes) |
| POST | `/api/aaa/network/sessions/classify-stale` | Mark sessions stale (interim window) |
| POST | `/api/aaa/network/sessions/detect-orphans` | Detect orphaned sessions |

## Control actions (CoA / Disconnect)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/control-actions` | Create a CoA/Disconnect control action (idempotent) |
| GET | `/api/aaa/control-actions` | List control actions |
| GET | `/api/aaa/control-actions/{action_id}` | Detail incl. ACK/NAK/timeout response |
| POST | `/api/aaa/control-actions/{action_id}/outcome` | Record outcome (ACK/NAK/TIMEOUT) |
| POST | `/api/aaa/control-actions/{action_id}/retry` | Retry a failed action |
| POST | `/api/aaa/control-actions/{action_id}/cancel` | Cancel a pending action |

## RouterOS / managed configuration

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/nas/{nas_id}/network-readiness` | Non-destructive readiness check + Winbox guide |
| GET | `/api/aaa/nas/{nas_id}/network-setup-requirements` | Latest Winbox setup requirements |
| POST | `/api/aaa/nas/{nas_id}/managed-config/read` | Read managed objects (queues/mangle/address lists) |
| POST | `/api/aaa/nas/{nas_id}/managed-config/diff` | Desired vs observed diff (simulation) |
| POST | `/api/aaa/nas/{nas_id}/managed-config/apply` | Apply only platform-managed QoS objects |
| POST | `/api/aaa/nas/{nas_id}/managed-config/verify` | Verify managed objects present |
| POST | `/api/aaa/nas/{nas_id}/managed-config/reconcile` | Record drift for missing managed objects |
| GET | `/api/aaa/nas/{nas_id}/policy-drift` | Policy drift records |
| POST | `/api/aaa/network/reconcile` | Classified session reconciliation (simulation only) |

## FUP

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/fup/subscribers/{subscriber_id}/usage` | Usage + active FUP tier |
| GET | `/api/aaa/fup/subscribers/{subscriber_id}/history` | FUP counter history |
| POST | `/api/aaa/fup/subscribers/{subscriber_id}/reset` | Reset FUP cycle (restore normal) |
| POST | `/api/aaa/fup/subscribers/{subscriber_id}/topup` | Apply a top-up |
| POST | `/api/aaa/fup/subscribers/{subscriber_id}/preview` | Preview resulting FUP policy/attributes |

## Bandwidth / QoS / FUP catalog

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/bandwidth-profiles` | Create bandwidth profile |
| GET | `/api/aaa/bandwidth-profiles` | List bandwidth profiles |
| POST | `/api/aaa/traffic-classes` | Create traffic class |
| GET | `/api/aaa/traffic-classes` | List traffic classes |
| POST | `/api/aaa/qos-profiles` | Create QoS profile |
| GET | `/api/aaa/qos-profiles` | List QoS profiles |
| POST | `/api/aaa/qos-profiles/{qos_id}/compile` | Compile QoS profile → managed objects |
| POST | `/api/aaa/fup-policies` | Create FUP policy |
| GET | `/api/aaa/fup-policies` | List FUP policies |

## IP identity

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/ip-identity/search` | Search identity (IP/username/MAC/session/NAS) |
| GET | `/api/aaa/ip-identity/{ip_address}/history` | IP ownership history |
| GET | `/api/aaa/ip-identity/{ip_address}/regulatory` | Authorized regulatory lookup (audited) |
