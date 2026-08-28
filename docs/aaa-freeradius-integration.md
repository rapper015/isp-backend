# AAA / FreeRADIUS REST integration contract

The AAA service implements a private JSON contract for a manually managed
FreeRADIUS deployment. It does **not** install, configure, start, stop, reload,
or otherwise manage FreeRADIUS.

## Required environment

Set AAA_INTERNAL_API_KEY to a strong service-to-service secret and
AAA_TRUSTED_SOURCES to a comma-separated allowlist of adapter source IPs. Set
AAA_ENCRYPTION_KEY to a Fernet key before enabling encrypted NAS secrets or
protocol-specific credential material. DATABASE_URL, RABBITMQ_URL, and
VALKEY_URL are supplied by the platform environment.

See services/aaa-service/.env.example for the complete non-secret variable
template and operational defaults.

Apply database migrations before running the service with Alembic from the AAA
service directory. The migration never includes plaintext credentials or shared
secrets.

For zero-downtime service-key rotation, set AAA_INTERNAL_API_KEYS to a
comma-separated list of current and next keys, deploy the adapter with the next
key, then remove the old key. Set AAA_MTLS_IDENTITIES when the upstream proxy
validates client certificates and forwards an approved certificate identity.

## Private endpoints

All endpoints use the X-AAA-Service-Key header and should only be reachable on
the private network:

- POST /internal/radius/v1/authenticate
- POST /internal/radius/v1/authorize
- POST /internal/radius/v1/accounting
- POST /internal/radius/v1/post-auth
- GET /internal/radius/v1/health
- GET /internal/radius/v1/readiness

Each request has a correlation_id, optional idempotency_key, and an attributes
object. Authentication uses User-Password for PAP. The service validates and
allowlists attributes, resolves the NAS first, obtains the tenant solely from
that trusted NAS record, and never falls back to another tenant.

Successful authorization replies are limited to supported attributes, including
Mikrotik-Rate-Limit, Framed-IP-Address, Framed-Pool, Session-Timeout,
Idle-Timeout, Acct-Interim-Interval, Filter-Id, and VLAN tunnel attributes.
Unknown inbound attributes are excluded from decisions and replies; they may only
appear in redacted diagnostics.

PAP is the currently implemented credential-verification method. MAC access is
available only for an explicitly MAC-bound credential and NAS that permits it.
CHAP and MS-CHAPv2 are deliberately rejected until a protocol-specific verifier
and encrypted recoverable secret workflow are configured; a one-way bcrypt hash
must never be treated as valid CHAP/MS-CHAP secret material.

## Sanitized contracts

Authentication request example:

    POST /internal/radius/v1/authenticate
    X-AAA-Service-Key: supplied-out-of-band
    {"correlation_id":"request-123","attributes":{"User-Name":"alice",
    "User-Password":"provided-by-nas","NAS-IP-Address":"10.20.0.1",
    "NAS-Identifier":"edge-1","Service-Type":"pppoe"}}

Acceptance response example:

    {"outcome":"Access-Accept","decision":"ACCEPT",
    "reply_attributes":{"Mikrotik-Rate-Limit":"10240k/51200k",
    "Acct-Interim-Interval":300},"control_attributes":{},
    "correlation_id":"request-123"}

Rejection responses intentionally use a generic reply message or no reply
message. The internal decision code is for trusted adapter diagnostics only and
must not be exposed to subscribers.

Accounting request example:

    POST /internal/radius/v1/accounting
    X-AAA-Service-Key: supplied-out-of-band
    {"idempotency_key":"nas-event-unique-key","attributes":{
    "User-Name":"alice","NAS-IP-Address":"10.20.0.1",
    "Acct-Session-Id":"session-123","Acct-Status-Type":"Interim-Update",
    "Acct-Input-Octets":1234,"Acct-Input-Gigawords":0,
    "Acct-Output-Octets":5678,"Acct-Output-Gigawords":0}}

Use a two-second adapter timeout and at most two bounded retry attempts for
authentication and authorization. Accounting retransmissions must reuse the
same idempotency key when one is available. An accounting request is accepted
only after its database event and transactional outbox record have been stored.

## Accounting behavior

Accounting accepts Start, Interim-Update, Stop, Accounting-On, and
Accounting-Off. It persists an immutable event and transactional outbox record
before returning OK. Duplicate retransmissions use the supplied idempotency key
or a deterministic normalized-packet key. Traffic counters combine octets and
gigawords as unsigned 64-bit values. No RabbitMQ call is made in the synchronous
authentication path.

## Manual FreeRADIUS work later

An administrator must manually configure a REST adapter to call these private
URLs, set short timeouts, pass the service header, and configure the same NAS
shared secret on the NAS and FreeRADIUS. This repository deliberately provides
no active FreeRADIUS configuration, container, or deployment command.

## AAA RabbitMQ topology

The backend declares durable topic exchanges aaa.events.v1, aaa.retry.v1, and
aaa.dead.v1. Accounting and command workers use durable work queues with
30-second retry queues and dead-letter queues. Event envelopes contain the
event ID, type, schema version, timestamps, tenant ID, correlation ID,
idempotency key, producer, and payload. Credential and shared-secret material
must never be added to an event payload.

## AAA worker and management APIs

`aaa-worker` is a separate bounded worker process. It detects stale sessions,
evaluates registered RADIUS-server heartbeats, publishes the transactional
outbox, and delivers at most one queued CoA/Disconnect command per cycle. NAS
packet delivery is disabled by default. Set `AAA_ENABLE_RADIUS_COMMANDS=true`
only after the NAS inventory and encrypted shared secret are deliberately
configured; it does not alter FreeRADIUS.

Privileged management endpoints live under `/api/aaa/`. They include tenant
scoped NAS CRUD and activity, credential lifecycle, policy previews and safe
eligibility simulations, session search/detail/reconciliation planning, command
queueing, accounting replay, usage, IP pools, and logical RADIUS registration.
Use `tenant_id` on tenant-owned resources. The reconciliation endpoint accepts a
trusted RouterOS snapshot supplied by an authorized caller and returns a plan;
it never connects to RouterOS or modifies a router itself.

Management calls can use the internal service key for trusted platform-to-
platform work, or a signed platform admin JWT (`Authorization: Bearer ...`).
Set `AAA_JWT_SECRET` to the existing platform admin-token signing secret. JWT
roles are mapped to granular AAA permissions; non-super-admin tokens carrying
`tenant_id`/`tenantId` are restricted to that tenant's query or request body.

Before connecting an external RADIUS service, create the tenant, NAS inventory
record, subscriber credential, plan policy, and any required IP pool through
the AAA APIs. Record the NAS source IP and identifier accurately: those trusted
values determine tenant resolution. Configure the same rotated shared secret
manually on the NAS and in the future RADIUS environment. Confirm health and
readiness endpoints from the private adapter network, then test with a
non-production subscriber. Do not expose the private OpenAPI endpoint or
service-key header through the public gateway.
