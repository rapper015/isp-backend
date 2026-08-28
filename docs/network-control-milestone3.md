# Milestone 3 — Advanced Network Control

## 1. Repository audit (what already exists)

The platform is the **FastAPI microservice monorepo** (not Django). Milestones 0-2
are complete on `aaa-service` (NAS/RouterOS, RADIUS, sessions, encryption),
`crm-service`, `bss-service`, `oss-service` (orders/sagas/resources) and
`ipam-service`. Milestone 3 is implemented inside the **AAA bounded context** —
the spec explicitly says not to create a duplicate application when an
appropriate module already exists.

Existing AAA components relevant to M3 (all `KEEP`/`EXTEND`):

| Component | State | Decision |
| --- | --- | --- |
| `app/policy.py` `calculate_policy` + `EffectivePolicy.reply_attributes` | Precedence tuple + RADIUS reply rendering | **EXTEND + FIX** — rate-limit direction inverted (`upload/download`) |
| `app/radius.py` allowlists + `safe_reply` | Attribute validation / reply allowlist | **KEEP** |
| `app/models.py` `ActiveSession`, `AccountingEvent`, `UsageProjection`, `RadiusCommand` (CoA/Disconnect) | Session registry + CoA/Disconnect command queue | **EXTEND** — timeline, stale/orphan, control-action registry with ACK/NAK/timeout |
| `app/models.py` `IpPool`/`IpLease`, `app/ipam.py` | IP ownership | **KEEP** (authoritative) |
| `app/models.py` `Nas`, `NasDesiredConfiguration`, `NasChangePlan`, `NasJob`, `NasCapability`, `NasRemoteObject`, `NasOperationLock` | Desired-state + config jobs + drift | **KEEP** — reuse for device diff/apply/verify/rollback |
| `app/routeros.py` `RouterOSAdapter` ABC + `RouterOSApiAdapter` + `FakeRouterOSAdapter` | Secure transport abstraction | **EXTEND** — typed operations allowlist, readiness check, Winbox guide, QoS compilation target |
| `app/services.py` `effective_subscriber_policy` | FUP as final quota layer | **EXTEND** — full precedence engine + explainable decisions |
| `app/events.py` outbox/inbox, `app/cache.py` | Messaging + Redis (best-effort) | **EXTEND** — M3 event contracts, compiled-policy cache |
| `app/security.py` Fernet, RBAC, internal auth | Secrets + authz | **KEEP** — add M3 permissions |

## 2. What is broken / must be repaired for M3

1. **MikroTik rate-limit direction is inverted** in `app/policy.py`
   (`f"{upload}k/{download}k"`). RouterOS `rx/tx` is from the router's
   perspective, so the attribute must be `download/upload`. Fixed + unit tested.
2. **No vendor-neutral policy model** — policies were ad-hoc dicts on `Tenant`
   and ad-hoc `calculate_policy` layers. M3 introduces the full policy model.
3. **No explainable policy decisions** — M3 adds `PolicyDecision` persistence.
4. **CoA/Disconnect have no ACK/NAK/timeout outcome tracking** — M3 adds the
   `ControlAction` registry with attempt/outcome persistence and retry policy.
5. **No FUP as a first-class policy** — M3 adds `FairUsagePolicy`/`FupCounter`.
6. **No QoS model / typed RouterOS operations allowlist** — M3 adds these.
7. **No readiness check / Winbox guide** — M3 adds non-destructive checks.
8. **No session timeline projection / stale & orphan detection** — M3 adds.

## 3. What is preserved (no duplication)

- IPAM (`aaa.ip_pools`/`aaa_ip_leases`) remains authoritative for IP ownership.
- `RadiusCommand`/worker CoA path is retained; M3 `ControlAction` wraps it.
- NAS desired-state/config jobs (`NasDesiredConfiguration`/`NasChangePlan`/`NasJob`)
  are reused for device diff/apply/verify/rollback.
- RouterOS transport adapters are reused; no new transport.
- No FreeRADIUS deployment/config modified.

## 4. What is implemented (Milestone 3)

- Policy model: `NetworkPolicy`, `NetworkPolicyVersion`, `BandwidthProfile`,
  `QosProfile`, `TrafficClass`, `FairUsagePolicy`, `FupCounter`,
  `SubscriberPolicyAssignment`, `PolicyOverride`, `PolicyDecision`,
  `EnforcementAction`, `EnforcementAttempt`, `DevicePolicyBinding`,
  `PolicyDriftRecord`, `ControlAction`, `SessionTimeline`, `RouterReadinessReport`.
- Deterministic precedence engine with explainable decisions.
- RADIUS policy compiler (direction-correct rate limit, bursts, IP, timeouts).
- FUP threshold/tier/top-up/reset engine.
- QoS compiler (traffic classes, marks, queue-type objects tagged `managed-by=isp-platform`).
- Typed RouterOS operations allowlist + readiness check + Winbox setup guide.
- Control-action registry (CoA/Disconnect) with ACK/NAK/timeout persistence, retry, idempotency.
- Session timeline, stale/orphan detection, reconciliation classification.
- IP identity / regulatory lookup with strict RBAC + audit.
- RabbitMQ event contracts, Redis compiled-policy cache.
- `/api/aaa/...` APIs, migration `0006_network_control.py`, tests, runbook.

## 5. Definition of Done coverage

All M3 DoD items are addressed; tests run without a live router
(`AAA_ROUTEROS_ADAPTER=fake`); deterministic fakes are used in tests only, the
production path uses typed operations against the real adapter.

## 6. Final verification report (spec §32)

- **What already existed (preserved):** NAS/RouterOS adapters + desired-state +
  drift + config jobs (M0); RADIUS auth/authorization/accounting + session
  registry + IP pools/leases + encryption (M0); order/saga/resource and CRM
  integration points (M1/M2). No FreeRADIUS deployment/config was modified.
- **What was broken (repaired):** `Mikrotik-Rate-Limit` upload/download
  inversion in `app/policy.py` (now direction-correct via the shared compiler;
  regression tests added); no explainable policy decisions (added); no
  ACK/NAK/timeout persistence for CoA/Disconnect (added `ControlAction`);
  FUP was not first-class (added); no QoS model or typed RouterOS allowlist
  (added); no readiness/Winbox guide (added); no session timeline / stale /
  orphan detection (added).
- **Models/migrations added:** `0006_network_control.py` adds 17 `nc_*` tables
  (policies, policy versions, bandwidth profiles, traffic classes, QoS
  profiles, FUP policies/counters, subscriber assignments, overrides,
  decisions, enforcement actions/attempts, control actions, session timeline,
  device bindings, policy drift, router readiness).
- **APIs added/changed:** `/api/aaa/policies*`, `/api/aaa/bandwidth-profiles*`,
  `/api/aaa/traffic-classes*`, `/api/aaa/qos-profiles*`, `/api/aaa/fup-policies*`,
  `/api/aaa/subscribers/{id}/policy-assignment|overrides|effective-policy/explain`,
  `/api/aaa/network/sessions*`, `/api/aaa/control-actions*`,
  `/api/aaa/nas/{id}/network-readiness|network-setup-requirements|managed-config/*|policy-drift`,
  `/api/aaa/network/reconcile`, `/api/aaa/fup/subscribers/*`,
  `/api/aaa/ip-identity/*`. Existing internal RADIUS endpoints unchanged.
- **RADIUS attributes supported:** `Mikrotik-Rate-Limit` (direction-correct,
  with bursts), `Mikrotik-Group`, `Mikrotik-Mark-Id`, `Mikrotik-Address-List`,
  `Framed-IP-Address`, `Framed-Pool`, `Framed-IPv6-Prefix/Pool`, `Filter-Id`,
  `Session-Timeout`, `Idle-Timeout`, `Acct-Interim-Interval`, `Simultaneous-Use`,
  `Tunnel-Private-Group-Id`.
- **RouterOS operations supported (allowlisted/typed):** connection test,
  discovery/capabilities, reads (radius config, incoming, ppp aaa, hotspot
  profiles, active PPP/hotspot sessions, IP pools, queues, queue types, queue
  trees, mangle, address lists), create/remove managed objects (queue type,
  queue tree, PCQ, mangle, address list), precise session disconnect, policy
  verify. Reboot/factory-reset/script/firewall-replace/console are prohibited.
- **CoA limitations/fallbacks:** live rate/group/filter/mark/timeout changes use
  CoA; IP/pool/route changes are flagged `DISCONNECT_AND_REAUTHORIZE` and are
  never presented as a successful CoA.
- **Test results:** `services/aaa-service` — **164 passed** (M0 101 + M3 63).
- **Router settings still required through Winbox:** API-SSL/REST + least-priv
  user, RADIUS client entry, PPP AAA use-radius+accounting, `/radius/incoming
  accept`, hotspot radius (where used), NTP (see runbook).
- **Remaining external dependencies:** live RADIUS CoA/Disconnect sender (the
  worker/AAA response path records real ACK/NAK/timeout; no live router is
  used by tests), live RouterOS connectivity (only via `AAA_ROUTEROS_ADAPTER`
  non-fake), RabbitMQ/Redis (fail-open). Consumers are idempotent (inbox).
- **Genuine blockers:** none for the implemented scope. Live CoA/Disconnect
  transport is exercised only when a lab router + RADIUS server are configured.

