# Microservice migration architecture

The platform is transitioning from a Django modular monolith to services by
bounded context. The gateway is the only public entry point. Each extracted
service owns its data and publishes versioned events using the envelope in
`shared/contracts`.

```
Clients / FreeRADIUS
        |
     Gateway
        |------------------- core-platform (legacy Django API) --- isp DB
        |------------------- CRM service ------------------------- crm DB
        |------------------- BSS service ------------------------- bss DB
        |------------------- OSS / AAA / NMS / IPAM / SIEM
        |                     Workforce / Warehouse / AIOps ------ own DBs
        |
     RabbitMQ (durable versioned domain events)
     Valkey   (cache, rate limits, locks, idempotency keys)
```

The first production-safe phase keeps API routes and data behaviour unchanged.
Extract a context only after its foreign-key and synchronous dependencies have
been replaced by stable API/event contracts. Start with NMS or IPAM, then OSS,
CRM, BSS, AAA, SIEM, Workforce, Warehouse, and AIOps as their dependencies are
untangled. Database-per-service is mandatory after extraction; no service may
read another service's tables.

See [`core-platform-retirement.md`](core-platform-retirement.md) for the
required, route-level evidence before the legacy Django service is removed.

## State responsibilities

- PostgreSQL is the source of truth for each service's business data.
- RabbitMQ carries durable asynchronous domain events; consumers must be
  idempotent.
- Valkey is temporary shared state only: response caching, API rate limits,
  distributed locks, short-lived sessions, and idempotency keys. Never place
  invoices, payments, customer records, or the only copy of an event in it.

## CRM cutover

The first usable CRM aggregate is customers, franchises, branches, leads, KYC
document metadata, and lifecycle events. Export legacy customers only after a
backup and validation run:

```bash
docker compose exec core-platform python manage.py export_crm_customers --dry-run
docker compose exec core-platform python manage.py export_crm_customers
```

The customer exporter is idempotent by `customer_code`. It is intentionally a
one-time migration tool; do not remove `core-platform` until the remaining CRM
records, authentication, and API consumers are cut over.

Run locally:

```bash
copy services\\core-platform\\.env.example services\\core-platform\\.env
docker compose up --build
docker compose exec core-platform python manage.py migrate
```

If you have an existing local SQLite database, move `db.sqlite3` from the
repository root to `services/core-platform/db.sqlite3` before starting the
relocated application. It is ignored by Git and is not moved automatically.
