# VPS deployment guide: Milestone 1 to Milestone 10

This runbook starts with **Milestone 1 already running on a VPS** and upgrades
the same installation to later milestone branches. Run commands from the
repository root on the VPS. The examples assume Linux, Bash, Docker Engine,
and Docker Compose v2 (`docker compose`).

## 1. Understand the deployment model

The milestone branches are cumulative. `milestone-7`, for example, contains
Milestones 0 through 7. You may deploy only the final branch you need, provided
you create every missing database and run every applicable migration. For a
production system, sequential deployment is recommended because it gives you
a smaller verification and rollback boundary at each step:

```text
milestone-1 (already live)
  -> milestone-2 -> milestone-3 -> milestone-4
  -> milestone-5 -> milestone-6 -> milestone-7
  -> milestone-8 -> milestone-9 -> milestone-10
```

Important exceptions:

- **Milestone 5 is not deployable as a live Support API yet.** The branch adds
  `services/support-service`, but the service, worker, database, storage, and
  gateway route are not wired into Compose. This remains true on the current
  Milestone 10 branch. Treat Milestone 5 as a source-code delivery only until
  that wiring is implemented.
- **Milestone 7 expects GenieACS at `http://genieacs:7557`, but Compose does not
  define a `genieacs` service.** Compose currently hardcodes that provider and
  URL for both Device Management containers, so changing `.env` alone cannot
  fix it. Patch and push the Compose wiring before deploying Milestone 7.
- Never run `docker compose down -v` during an upgrade. It deletes the named
  PostgreSQL volume and other persistent data.

## 2. One-time VPS preparation

Confirm that the repository and current deployment are healthy:

```bash
cd /path/to/isp-backend
git status --short
git branch --show-current
git rev-parse HEAD
docker compose version
docker compose ps
df -h
```

Replace `/path/to/isp-backend` with the actual directory. Before switching
branches, `git status --short` should be empty. Do not discard VPS edits. Commit
them, move them to a safe backup, or deliberately reproduce them through `.env`
or an override file.

Fetch the branches and verify that the requested target exists:

```bash
git fetch --all --prune
git branch -r --list 'origin/milestone-*'
git merge-base --is-ancestor origin/milestone-1 origin/milestone-10
echo $?
```

The final command must print `0`; that confirms Milestone 10 descends from
Milestone 1.

## 3. Protect configuration and data before every upgrade

Set the milestone you are about to deploy. This example uses Milestone 2:

```bash
export TARGET_MILESTONE=2
export DEPLOY_STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
git rev-parse HEAD > "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.commit"
cp .env "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.env"
docker compose ps > "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.containers.txt"
docker compose exec -T postgres sh -c 'pg_dumpall -U "$POSTGRES_USER"' \
  > "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.sql"
test -s "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.sql"
```

Restrict the configuration backup because it contains secrets:

```bash
chmod 600 "backups/pre-milestone-${TARGET_MILESTONE}-${DEPLOY_STAMP}.env"
```

Copy the whole `backups/` directory to storage outside this VPS. A backup on the
same disk is not sufficient protection against disk loss.

## 4. Update `.env` safely

Never replace the production `.env` with `.env.example`; that would replace
working secrets and may break authentication. After fetching, inspect variables
added between the currently deployed branch and the target:

```bash
git diff origin/milestone-1..origin/milestone-2 -- .env.example
```

For later upgrades, change both branch names, for example:

```bash
git diff origin/milestone-6..origin/milestone-7 -- .env.example
```

Manually add missing settings to `.env`. Generate independent production
secrets; do not use any `change-me` Compose default:

```bash
openssl rand -hex 32
```

At minimum, review the JWT secrets, internal API keys, encryption keys,
PostgreSQL credentials, RabbitMQ credentials, initial administrator password,
public base URL, and CORS origins for every newly added service. Keep `.env`
outside Git and readable only by the deployment account:

```bash
chmod 600 .env
```

If the browser frontend is on another origin, use exact origins rather than
`*`, for example:

```dotenv
CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
```

An origin is scheme + host + optional port. Do not include a trailing slash or
API path. Confirm each newly exposed service actually consumes the shared CORS
setting before releasing its frontend; the current CORS integration work only
covers the Milestone 0/1 services.

## 5. The repeated upgrade procedure

Use this procedure for each target milestone. Replace `2` with the milestone
number being deployed.

### 5.1 Switch to the target branch

```bash
git checkout milestone-2
git pull --ff-only origin milestone-2
git status --short
```

For a first checkout on a VPS that has only remote branches:

```bash
git checkout --track origin/milestone-2
```

### 5.2 Validate configuration before changing containers

```bash
docker compose config --quiet
docker compose config --services
```

If this fails, fix the missing `.env` variable first. Do not continue with a
partially rendered Compose configuration.

### 5.3 Build images and keep infrastructure available

```bash
docker compose build
docker compose up -d postgres rabbitmq valkey
docker compose ps postgres rabbitmq valkey
```

Wait until PostgreSQL, RabbitMQ, and Valkey are healthy.

### 5.4 Create the new database

The `POSTGRES_MULTIPLE_DATABASES` setting is processed only when PostgreSQL
initializes an empty volume. Because the Milestone 1 volume already exists, it
**will not create later milestone databases automatically**.

List existing databases:

```bash
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d postgres -Atc "SELECT datname FROM pg_database ORDER BY datname"'
```

Create the database shown in the milestone table below only if it is absent:

```bash
docker compose exec -T postgres sh -c \
  'createdb -U "$POSTGRES_USER" oss'
```

If `createdb` reports that the database already exists, verify that it belongs
to this deployment and continue; do not delete or recreate it.

### 5.5 Run migrations before exposing the service

Run the milestone-specific migration commands from the table below. The image
must have been built first.

```bash
docker compose run --rm --no-deps oss-service alembic upgrade head
```

Alembic upgrades are incremental, so rerunning an applicable command is safe;
it leaves a database already at `head` unchanged.

### 5.6 Start the cumulative stack

```bash
docker compose up -d --remove-orphans
docker compose ps
```

Do not proceed while a required service is restarting or unhealthy. Inspect a
problem service with:

```bash
docker compose logs --tail=200 SERVICE_NAME
```

Replace `SERVICE_NAME` with the Compose name, such as `oss-service`.

## 6. Commands for each milestone

Run Section 3 before each row, then Sections 4 and 5 with the values below.

| Target | New runtime | Database to create | Migration command(s) | Extra prerequisite |
|---|---|---|---|---|
| 2 | `oss-service` | `oss` | `docker compose run --rm --no-deps oss-service alembic upgrade head` | Verify OSS orders and subscribers. |
| 3 | `nms-service` plus AAA network control | `nms` | `docker compose run --rm --no-deps aaa-service alembic upgrade head` and `docker compose run --rm --no-deps nms-service alembic upgrade head` | Re-test AAA after its new migration. |
| 4 | `bss-service` | `bss` | `docker compose run --rm --no-deps bss-service alembic upgrade head` | Verify plans, invoices, and payments. |
| 5 | Support source only | None in current Compose | None deployable through current Compose | See the Milestone 5 gap below. |
| 6 | `workforce-service`, `workforce-worker` | `workforce` | `docker compose run --rm --no-deps workforce-service alembic upgrade head` | Verify both API and worker stay healthy. |
| 7 | `device-management-service`, worker | `device_management` | `docker compose run --rm --no-deps device-management-service alembic upgrade head` | Configure fake or external GenieACS first. |
| 8 | `tenancy-service`, `tenancy-worker` | `tenancy` | `docker compose run --rm --no-deps tenancy-service alembic upgrade head` | Verify tenant isolation and worker. |
| 9 | `assurance-service`, `assurance-worker` | `assurance` | `docker compose run --rm --no-deps assurance-service alembic upgrade head` | Verify API and worker. |
| 10 | Intelligence, SIEM, Warehouse, AIOps, IPAM, and workers | `intelligence`, `siem`, `warehouse`, `aiops`, `ipam` | Run Alembic for `intelligence-service`, `siem-service`, and `warehouse-service` | AIOps and IPAM currently have no Alembic configuration. |

For Milestone 10, create its five databases idempotently with PostgreSQL's
`psql` client. Each statement creates its database only when it is absent:

```bash
docker compose exec -T postgres sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres' <<'SQL'
SELECT 'CREATE DATABASE intelligence'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'intelligence')\gexec
SELECT 'CREATE DATABASE siem'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'siem')\gexec
SELECT 'CREATE DATABASE warehouse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'warehouse')\gexec
SELECT 'CREATE DATABASE aiops'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'aiops')\gexec
SELECT 'CREATE DATABASE ipam'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ipam')\gexec
SQL
```

Then migrate the three services that contain Alembic configuration:

```bash
docker compose run --rm --no-deps intelligence-service alembic upgrade head
docker compose run --rm --no-deps siem-service alembic upgrade head
docker compose run --rm --no-deps warehouse-service alembic upgrade head
```

If jumping directly from Milestone 1 to a later milestone, create **all** of the
databases up to that target and run every migration command up to that row. For
example, a direct jump to Milestone 8 requires `oss`, `nms`, `bss`, `workforce`,
`device_management`, and `tenancy`, including both Milestone 3 migrations.

## 7. Milestone 5 Support API gap

Checking out `milestone-5` is safe, but it does not make Support reachable. A
real Support deployment first needs an implementation change that adds:

1. A `support`/`support_db` database and consistent `DATABASE_URL`.
2. `support-service` and its worker to `docker-compose.yml`.
3. Persistent attachment storage.
4. JWT, internal API key, RabbitMQ, and cross-service environment settings.
5. A Support upstream and `/api/...` route in the gateway.
6. Alembic migration execution and API/worker health checks.

Until those items are implemented and reviewed, deploy Milestone 5 as the same
runtime footprint as Milestone 4. Moving on to Milestone 6 does not implicitly
solve this gap.

## 8. Milestone 7 GenieACS decision

Before starting Milestone 7, change `docker-compose.yml` so both
`device-management-service` and `device-management-worker` read the provider
and URL from `.env`, instead of hardcoding them. For example, their environment
must resolve equivalent values to:

```yaml
ACS_PROVIDER: ${ACS_PROVIDER:-fake}
GENIEACS_BASE_URL: ${GENIEACS_BASE_URL:-http://genieacs:7557}
```

Commit and push that change to the milestone branch, pull it on the VPS, and
then choose one:

- For validation without a real ACS, set `ACS_PROVIDER=fake` in the effective
  Device Management environment/Compose configuration.
- For production, deploy GenieACS on a reachable Docker network or host and set
  `GENIEACS_BASE_URL` accordingly. If HTTPS is used, keep certificate
  verification enabled.

Confirm the effective setting before deployment:

```bash
docker compose config | grep -E 'ACS_PROVIDER|GENIEACS_BASE_URL'
```

Do not accept the current `http://genieacs:7557` default unless a container or
DNS endpoint named `genieacs` is actually reachable from the service network.

## 9. Post-deployment verification

### 9.1 Containers and logs

```bash
docker compose ps
docker compose logs --tail=100 gateway
docker compose logs --tail=100 SERVICE_NAME
```

All required API services and workers should be `Up`; health-enabled services
should become `healthy`. There should be no database authentication, missing
relation, connection-refused, invalid JWT secret, or migration errors.

### 9.2 Service health inside the Docker network

For a newly added API, run its own health endpoint inside its container:

```bash
docker compose exec -T SERVICE_NAME python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Use the exact health path defined by that service if it differs from `/health`.

### 9.3 Gateway routing

From the VPS, call the new route through the gateway. The following unauthenticated
request may return `401` or `403`; either proves the route reached an
authentication-aware application. `404` suggests a wrong route and `502`
suggests an unavailable upstream.

```bash
curl -i http://127.0.0.1:4000/api/oss/orders
```

Repeat with the applicable route: `/api/nms/`, `/api/bss/`,
`/api/workforce/`, `/api/device-management/`, `/api/tenancy/`,
`/api/assurance/`, `/api/intelligence/`, `/api/siem/`, or `/api/warehouse/`.
Then perform one authenticated read-only request and one milestone-specific
smoke test from the frontend/API collection.

### 9.4 Database migration state

```bash
docker compose run --rm --no-deps SERVICE_NAME alembic current
docker compose run --rm --no-deps SERVICE_NAME alembic heads
```

The current revision should match a head revision.

### 9.5 Browser CORS

Test from the real frontend origin, and also inspect a preflight response:

```bash
curl -i -X OPTIONS 'https://api.example.com/api/oss/orders' \
  -H 'Origin: https://app.example.com' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

The response must allow the exact frontend origin and requested headers. Do not
ship a wildcard origin with credentialed browser requests.

## 10. Rollback

First record evidence:

```bash
docker compose ps
docker compose logs --tail=300 > "backups/failed-${DEPLOY_STAMP}.log"
```

For an application-only rollback, switch to the previous branch and rebuild:

```bash
git checkout milestone-PREVIOUS
git pull --ff-only origin milestone-PREVIOUS
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose ps
```

Replace `milestone-PREVIOUS` with the known-good branch. Do not automatically
run `alembic downgrade`; schema downgrade safety depends on the exact migration
and data written after deployment. In many incidents, leaving additive tables
in place while rolling back application containers is safer.

Restore the SQL backup only when an application rollback is insufficient and
you have accepted losing writes made after the backup. Database restoration is
a maintenance-window operation. Stop application traffic, take one final
incident snapshot, and restore under an agreed recovery plan. Never use
`docker compose down -v` as a rollback mechanism.

## 11. Completion checklist for every milestone

- [ ] Repository was clean and the old commit ID was recorded.
- [ ] `.env`, database, and container-state backups were created off-host.
- [ ] New `.env` variables use production secrets, not Compose defaults.
- [ ] `docker compose config --quiet` passed.
- [ ] Missing databases were explicitly created on the existing volume.
- [ ] Every applicable Alembic migration reached `head`.
- [ ] Required APIs and workers are stable and healthy.
- [ ] The gateway route returns the expected authenticated response.
- [ ] Browser CORS works from the real frontend origin.
- [ ] A milestone-specific read-only smoke test passed.
- [ ] Monitoring and logs show no new recurring errors.
- [ ] The rollback commit and backup location are recorded.
