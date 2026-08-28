# Manual FreeRADIUS Registration and Secret Rotation

FreeRADIUS is configured manually by an administrator. The backend generates the
details, tracks manual confirmation separately from technical verification, and
never modifies FreeRADIUS.

## Registration package

For every NAS-to-RADIUS assignment
(`POST /api/nas/{id}/radius-assignments/{assignment_id}/registration-package`) a
secure package is produced containing:

```
NAS name, NAS short name, NAS source IP/CIDR, NAS-Identifier, Vendor,
RADIUS server, Authentication port, Accounting port, CoA port, Services,
Message-Authenticator requirement, Shared secret, Secret version,
Generated time, Expiry time (one-time viewing)
```

Requirements enforced:

* the shared secret is viewable only through a privileged one-time workflow
  (`POST .../registration-package/reveal` with a single-use, expiring token)
* the secret is never in logs, RabbitMQ, audit payloads or list APIs
* who accessed it and when is audited
* the token expires (`NasSecretReveal.expires_at`)
* regeneration is allowed while never applied
* the reveal returns both a copyable text representation and structured JSON

## Manual confirmation

`POST /api/nas/{id}/radius-assignments/{assignment_id}/confirm-registration`
records that an administrator confirmed: NAS added to FreeRADIUS, correct source
IP, correct shared-secret version, required services enabled, primary and
secondary servers configured. **Manual confirmation alone never marks the
registration verified.**

## Technical verification

`POST .../verify` records a functional signal:
`authentication_request_observed`, `accounting_request_observed`,
`integration_test_result`, or `freeradius_callback` containing the expected NAS
identity. Manual confirmation and technical verification are tracked separately;
only the latter moves the assignment to `VERIFIED`.

## Shared-secret rotation

FreeRADIUS is manual, so rotation is staged
(`POST .../rotate-secret`):

```text
ROTATION_DRAFT -> NEW_SECRET_GENERATED -> AWAITING_FREERADIUS_UPDATE
  -> FREERADIUS_UPDATE_CONFIRMED -> ROUTER_UPDATE_PENDING -> ROUTER_UPDATED
  -> VERIFYING -> ACTIVE
```

Workflow:

1. Generate a new assignment-specific secret; store it encrypted as pending.
2. Produce a one-time manual FreeRADIUS update package (reveal once).
3. Wait for administrator confirmation (`confirm-freeradius-update`).
4. Update the MikroTik assignment (`apply-secret`).
5. Verify RADIUS traffic (`verify`), then mark the new version active.
6. Retain the previous encrypted version for a short rollback window.
7. Delete/archive the previous secret according to policy.

The MikroTik secret is **never** changed before FreeRADIUS is prepared unless the
administrator explicitly chooses a planned-outage workflow. Rollback
(`rollback-secret`) restores the previous secret on the router and assignment.

Related endpoints:

```
POST /api/nas/{id}/radius-assignments/{a}/rotate-secret
POST /api/nas/{id}/radius-assignments/{a}/confirm-freeradius-update?rotation_id=...
POST /api/nas/{id}/radius-assignments/{a}/apply-secret?rotation_id=...
POST /api/nas/{id}/radius-assignments/{a}/rollback-secret?rotation_id=...
GET  /api/nas/{id}/radius-registration-status
```
