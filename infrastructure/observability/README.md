# Backend request logging and error tracking

All API entry points install `isp_shared.observability`. Worker entry points and the billing scheduler configure the same JSON logger. Docker retains bounded local logs; the existing optional Grafana stack still needs a working ingestion path for centralized log search.

## Find a failed request

1. Copy `X-Correlation-ID` from the failed request's response headers. Unhandled JSON 500 responses include `correlation_id` too.
2. Search backend logs for that value. Match `service`, `operation`, `status_code`, `error_class`, `stacktrace`, and any `job_id`/`workflow_id`.
3. Follow propagated IDs into the downstream service. Propagation currently covers onboarding and OSS-to-AAA credential/policy calls; other adapters/consumers require follow-up instrumentation.

PowerShell example:

```powershell
docker compose logs --since 30m tenancy-service crm-service oss-service aaa-service | Select-String 'PASTE_CORRELATION_ID'
```

Logs use route templates rather than raw request URLs. They exclude request/response bodies and query strings. Exception frames include file, function and line; arbitrary exception values and locals are omitted because SQL/HTTP errors may contain credentials and customer data. Existing business audit/database records have their own retention and redaction requirements.

## Enable Sentry or a compatible tracker

Create a Python project in your chosen Sentry installation and configure its DSN privately in the deployment `.env`:

```dotenv
LOG_JSON=true
ENVIRONMENT=production
APP_RELEASE=<deployed-git-sha>
SENTRY_DSN=<project-dsn>
```

Rebuild/recreate the affected API and worker services through the normal deployment process. With an empty DSN there is no remote export. This change does not install a Sentry server or create a SaaS project.

The SDK captures ERROR/exception logs: unhandled requests, HTTP 5xx completions and instrumented worker failures. Ordinary 4xx responses are warning logs, not Sentry issues. Other business failures returned as HTTP 200 need explicit ERROR logging at their decision boundary. Not every legacy path has this yet.

The Sentry event allowlist retains exception types/frame locations, service, environment, release and correlation/operation tags. It removes request data, users, cookies, headers, query strings, breadcrumbs, exception values, arbitrary extras and local variables. Tracing/profiling export is off. Scope error groups by service and operation; keep request IDs as tags rather than fingerprints.

Suggested tracker alerts: first occurrence after a release; repeated failures on tenant/franchise/customer onboarding; repeated NAS job failures; provisioning failures. Configure notification recipients in the tracker. Log instrumentation does not configure alert destinations automatically.

The integration follows the [official Sentry Python options](https://getsentry.github.io/sentry-python/api.html) and [logging integration](https://getsentry.github.io/sentry-python/_modules/sentry_sdk/integrations/logging.html).

## Verification

```powershell
$env:PYTHONPATH='shared/python'
.venv/Scripts/python.exe -m unittest discover -s shared/tests -v
docker compose config --quiet
```

The unit suite uses an in-memory Sentry transport, not a live remote destination. It checks concurrent-request isolation, correlation propagation, validation/500 responses, redaction and error-event delivery to the SDK transport. A live project ingestion test still needs a configured DSN and a running deployment.

For the all-service import test, also install `services/platform-core-service/requirements.txt` (it adds Argon2). The import test never runs service lifespan hooks or contacts a database. The NAS cleanup regression test uses mocked adapters and sessions.

Verified on 27 October 2026: 13 tests passed, including imports of all 16 APIs and the in-memory Sentry event test. `docker compose config --quiet` and `git diff --check` passed. Docker engine was unavailable, so containers and live tracker ingestion were not tested.

Do not create a public crash-test API. Verify a controlled error in a local/staging environment, then check its service, release, correlation ID and absence of sensitive values in the tracker.

## Tenant isolation and confirmed delivery follow-up

BSS and OSS management sessions now validate all supplied tenant IDs and apply
tenant restrictions to ORM reads and mutations. Tenant-scoped wildcard permissions
do not grant platform-wide access. Internal service keys remain trusted; portal
routes retain their separate customer ownership checks. Raw SQL and cross-service
references are not protected by this ORM guard and still need explicit validation.

OSS, Support, Tenancy, Device Management, Assurance, Intelligence, SIEM and Workforce now use a
confirmed RabbitMQ outbox publisher. Configure `RABBITMQ_URL` and bind durable
consumer queues to each service's `<service>.events.v1` topic exchange before
enabling delivery. Missing configuration, unroutable messages and missing broker
acknowledgements leave events pending. `OUTBOX_PUBLISH_TIMEOUT_SECONDS` defaults
to 5. Stable event IDs support consumer deduplication; delivery is at-least-once,
not exactly-once. Do not reset historical published markers automatically.

Regression coverage includes tenant query/body conflicts, scoped wildcard access,
cross-tenant entity and child reads, forbidden writes, scoped bulk deletion, and
failed publication followed by a successful retry with the same event ID.

Tenant onboarding is now resumable: it persists the local tenant before remote
work, records each stage, and creates the platform administrator with a stable
idempotency key. Platform Core stores only an HMAC request fingerprint, never the
password. Apply migration `0015_user_idempotency` before using this path and set
`PLATFORM_IDEMPOTENCY_SECRET` to a private 32+ character value (it falls back to
the JWT secret for compatibility).

Franchise onboarding follows the same pattern and remains inactive until its
administrator and access template are confirmed. Apply CRM migration
`0010_franchise_onboarding`. OSS now honors the create-order `idempotency_key`
declared by its API; apply OSS migration `0004_order_create_idempotency`.

Still outstanding: resumable customer onboarding (including uploaded-file and
OSS activation recovery), automatic consistency reconciliation, remaining
service authorization audits, and live PostgreSQL/RabbitMQ integration tests.
These changes must not be represented as a complete backend consistency repair.
