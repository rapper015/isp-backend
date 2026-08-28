# New service template

Each extracted service must have its own Dockerfile, runtime dependencies,
database, migrations, health endpoint, and API/event contracts. Do not import
models or source modules from another service.

Required environment variables:

- `SERVICE_NAME`
- `DATABASE_URL`
- `LOG_LEVEL`

Before adding a cross-service call, define the request/response or event schema
in `shared/contracts` and keep retries, idempotency, and correlation IDs at the
service boundary.
