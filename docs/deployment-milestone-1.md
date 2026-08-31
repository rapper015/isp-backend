# Hosting Milestone-1 + Frontend Developer Auth

Milestone-1 is a cumulative branch: it contains the platform foundation plus
**milestone-0** (`aaa-service` FreeRADIUS, `identity-service` app-user auth) and
**milestone-1** (`crm-service`). This is the first milestone a frontend team can
build against, because `identity-service` gives them **register + login** so they
can use the CRM APIs without any admin provisioning.

## What is in milestone-1

| Service | Role |
|---|---|
| `gateway` (nginx) | Single entry point, port **4000** |
| `postgres`, `rabbitmq`, `valkey` | Shared infra |
| `identity-service` | App-user auth: **register**, **login**, `/me` → issues JWT |
| `aaa-service` (+worker) | NAS/RADIUS (FreeRADIUS) management |
| `crm-service` | Customer / lead lifecycle APIs |

## 1. Deploy (one time, as the operator)

```bash
git clone https://github.com/rapper015/isp-backend.git
cd isp-backend
git checkout milestone-1

cp .env.example .env
```

Edit `.env` and set at least:

```env
# One shared secret (>= 32 chars) — this is what identity signs tokens with
# and what CRM/AAA verify them with.
PLATFORM_JWT_SECRET=change-me-to-a-long-random-string-0123456789abcdef

# Let frontend developers self-register and give them CRM write access.
IDENTITY_ALLOW_REGISTRATION=true
IDENTITY_REGISTRATION_ROLE=CRM_MANAGER

# First admin (also created at startup).
IDENTITY_BOOTSTRAP_ADMIN_USERNAME=admin
IDENTITY_BOOTSTRAP_ADMIN_PASSWORD=change-me-admin-password
```

> **Postgres credentials (important).** The compose wires `POSTGRES_USER` /
> `POSTGRES_PASSWORD` consistently everywhere. If you want a strong DB
> password, set `POSTGRES_PASSWORD` in `.env` **and** run `docker compose
> down -v` before `up` so postgres re-initializes with it (the postgres data
> volume is only seeded on first init). If you set `POSTGRES_PASSWORD` after
> postgres was already initialized with a different value, every service that
> connects to the DB fails with `password authentication failed` — and
> `identity-service` is the first to crash because it connects at startup.

Then:

```bash
docker compose up -d --build
docker compose ps                     # all services healthy
curl http://localhost:4000/health     # gateway reachable
```

The API base URL for the frontend is:

```
http://<your-server-ip-or-domain>:4000
```

## 2. Auth flow for the frontend developer

The developer only needs three calls — no admin involvement:

### a) Register (self-service)

```http
POST http://<host>:4000/api/auth/register
Content-Type: application/json

{
  "username": "dev1",
  "password": "DevPass!234",
  "email": "dev1@company.com",
  "full_name": "Frontend Dev"
}
```

Response (201):

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": { "id": "...", "username": "dev1", "role": "CRM_MANAGER" }
}
```

The account gets role `CRM_MANAGER` (from `IDENTITY_REGISTRATION_ROLE`), which can
create/view/update leads and customers. Public signup can **never** escalate its
own role.

### b) Login (when the token expires)

```http
POST http://<host>:4000/api/auth/login
Content-Type: application/json

{ "username": "dev1", "password": "DevPass!234" }
```

Returns the same shape as register.

### c) Use the token on CRM APIs

Send every `/api/crm/*` request with:

```
Authorization: Bearer <access_token>
```

Examples:

```bash
# list customers
curl -H "Authorization: Bearer $TOKEN" http://<host>:4000/api/crm/customers

# create a customer
curl -X POST http://<host>:4000/api/crm/customers \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Acme","type":"RESIDENTIAL"}'

# who am I
curl -H "Authorization: Bearer $TOKEN" http://<host>:4000/api/auth/me
```

Other CRM endpoints (from `docs/apis/milestone-1-crm.md`):
- `POST/GET /api/crm/leads`, `POST /api/crm/leads/{id}/convert`
- `GET /api/crm/customers/{id}/360`, `GET /api/crm/customers/{id}/timeline`
- `POST /api/crm/customers/{id}/interactions`, `/follow-ups`

## 3. How auth works (so you can debug)

- `identity-service` signs JWTs with `PLATFORM_JWT_SECRET`.
- `crm-service` and `aaa-service` verify management JWTs with `CRM_JWT_SECRET` /
  `AAA_JWT_SECRET`, which **default to `PLATFORM_JWT_SECRET`** in `docker-compose.yml`.
  One token works everywhere.
- Token claims: `userId`, `username`, `role`, `permissions`, `iat`, `exp`.
- Permissions are checked per-endpoint against the role (e.g. `crm.customer.create`,
  `crm.lead.view`, …).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `FATAL: password authentication failed for user \"isp\"` | `POSTGRES_PASSWORD`/`POSTGRES_USER` in `.env` don't match what postgres was initialized with. Either set them to `isp` (dev) or, for a custom password, run `docker compose down -v` so the volume re-initializes |
| `401 management authentication failed` on CRM | `CRM_JWT_SECRET` overridden in `.env` to something ≠ `PLATFORM_JWT_SECRET` — remove the override |
| `503 management authentication is not securely configured` | `PLATFORM_JWT_SECRET` < 32 chars or empty |
| `403 CRM permission denied` | Role lacks the permission (e.g. `READ_ONLY` can only view) — set `IDENTITY_REGISTRATION_ROLE=CRM_MANAGER` |
| `403 registration is disabled` | `IDENTITY_ALLOW_REGISTRATION` is not `true` |
| `429 rate limit exceeded` | Too many requests — wait 60s |
| `409 username already exists` | That username is taken — pick another |

See `docs/identity-auth.md` for the auth design and `docs/milestone-cumulative-model.md`
for how milestones build on each other.
