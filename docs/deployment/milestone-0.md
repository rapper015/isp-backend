# Milestone 0 deployment

Create a private `.env` from `.env.example` with real database, broker,
encryption and platform-token secrets. Run AAA and platform-core migrations
before starting traffic, then `docker compose up -d`. Only the gateway is
published; service and RADIUS-management contracts stay on `platform`.

Upgrade: back up PostgreSQL, run migrations, verify `/health` for gateway,
platform core and AAA, then test an operator login and an AAA management call.
Never run `docker compose down -v` during upgrade. Roll back application images
and restore the backup only if migration rollback is unsafe.
