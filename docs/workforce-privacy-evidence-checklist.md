# Workforce Privacy & Evidence Checklist (Milestone 6)

Guidance for field technicians, QA reviewers and engineers on how the
workforce service protects customer privacy and preserves verifiable proof of
work. Everything below is enforced by the service; this document explains the
design so teams operate within it.

## Privacy principles

1. **Least privilege on the technician app.** Technician endpoints return only
   the customer information needed for the work (name, address, subscription,
   service location, instructions). Internal notes, root-cause summaries and
   score breakdowns are **not** sent to the technician app.
2. **Privacy-safe customer portal.** `GET /portal/work-orders/{id}` returns
   status, appointment window, expected arrival deadline and result code — it
   never exposes exact technician GPS coordinates, internal notes, proof files
   or the technician's name.
3. **No live location broadcast.** Work-order coordinates are the customer's
   service location, stored only to enable geofenced check-in. There is no
   continuous technician location feed.
4. **Masked customer identity.** Acknowledgement records store a masked
   recipient (e.g. `cus***01`) and consent text version — never raw PII unless
   required by the acknowledgment method, and never beyond retention.
5. **Tenant isolation.** Every read/write is tenant-scoped from the
   authenticated principal; mismatched `tenant_id` is rejected.

## GPS & geofence governance

- Check-in requires coordinates **or** a governed exception reason.
- Out-of-geofence, low-accuracy or invalid coordinates are rejected.
- Legitimate exceptions (indoor GPS, rural low accuracy, offline, wrong
  recorded location, infrastructure work) are recorded **with the exception
  reason** — the system never encourages fabricated coordinates.
- Supervisor overrides are audited via the immutable audit log.
- Geofence radius defaults to 500 m and can be overridden per service area.

## Proof of work

- Proof records carry **server-side** metadata: checksum, capture timestamp,
  device/session reference, GPS refs and verification state. Client-supplied
  values are never trusted alone.
- Duplicate uploads are rejected idempotently via a tenant-scoped
  `evidence_key`.
- Proof is required before execution completes (per work-order type:
  e.g. photograph + serial number + customer acknowledgement for new
  installations).
- Media is stored in private storage (`WORKFORCE_ATTACHMENT_DIR`). Download is
  authorization-controlled; there are **no permanent public URLs**. Allowed
  types and a size limit are enforced; quarantine/retention rules apply.

## Customer acknowledgement

- Acknowledgment methods are constrained (OTP, authorized-contact
  confirmation, signature, photograph consent, document reference, supervisor
  override).
- The recorded recipient is masked; the consent text version is stored so the
  exact wording agreed to is retained.

## Checklist and materials

- The exact checklist version used is snapshotted on the work order; published
  versions are immutable.
- Required materials must be reconciled (usage >= requirement) before
  completion — preventing "claimed done but never used the part" gaps.
- One device (serial/MAC) cannot be installed on two active services; installs
  go through the authoritative inventory adapter.

## Field technician responsibilities

- Record a real GPS check-in (or a truthful exception reason).
- Capture genuine proof before finishing execution.
- Never bypass the checklist — incomplete checklists block completion.
- Never photograph unrelated people or property; capture only what is needed
  for the work.
- Never share internal notes, score breakdowns or other technicians' data.
