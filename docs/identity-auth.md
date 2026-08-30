# Platform Authentication (Application Users)

The ISP platform has **two completely separate authentication domains**:

| Domain | Owned by | Who | Credentials |
|--------|----------|-----|-------------|
| **Application users** (operators, admins, NOC, sales, KYC) | `identity-service` | People who *use* the platform | username + password → **JWT** |
| **Subscriber/NAS auth** (FreeRADIUS) | `aaa-service` | Routers (NAS) and subscribers at the RADIUS edge | PAP/CHAP/EAP against RADIUS |

These are deliberately decoupled. The AAA service is a **fundamental of FreeRADIUS**
and is kept away from the platform's own IAM. Application-user auth lives in its
own microservice (`identity-service`, tables `idp_*`).

## Identity Service

`services/identity-service/` — FastAPI, SQLAlchemy, bcrypt + PyJWT.

### Environment
```env
PLATFORM_JWT_SECRET=<shared, >= 32 chars>     # used for signing if set
IDENTITY_JWT_SECRET=<fallback signing secret> # >= 32 chars
IDENTITY_TOKEN_TTL_SECONDS=43200              # token lifetime (seconds)
IDENTITY_INTERNAL_API_KEY=<service key>       # used by POST/GET /api/auth/users
IDENTITY_BOOTSTRAP_ADMIN_USERNAME=admin       # created at startup if table empty
IDENTITY_BOOTSTRAP_ADMIN_PASSWORD=<password>
```

> **One token platform-wide:** login is signed with `PLATFORM_JWT_SECRET`.
> Each other service verifies management JWTs with its own `<SVC>_JWT_SECRET`.
> Set all of them to the **same value** so a single login token authorizes
> across every service's management API.

### Endpoints (all behind the edge gateway unless noted)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/auth/login` | public | username + password → `{access_token, ...}` |
| POST | `/admin-login` | public | alias used by the nginx `POST /api/v1/auth/login` mapping |
| POST | `/api/auth/users` | service key | create an application user |
| GET  | `/api/auth/users` | service key | list application users |
| GET  | `/api/auth/me` | Bearer JWT | who am I (current user + role + tenant) |

### Gateway mapping (`infrastructure/gateway/nginx.conf`)
```nginx
location = /api/v1/auth/login { proxy_pass http://identity_service/admin-login; }
location /api/auth/         { proxy_pass http://identity_service/api/auth/; }
```

### JWT claims
```json
{
  "userId": "uuid",
  "username": "alice",
  "role": "PLATFORM_ADMIN",
  "permissions": ["*"],
  "tenant_id": "uuid-or-absent",
  "iat": 1690000000,
  "exp": 1690043200
}
```

Roles are interpreted per service via each service's `ROLE_PERMISSIONS` map.
`PLATFORM_ADMIN` / `ISP_OWNER` / `ISP_ADMIN` map to `{"*"}`.

## AAA stays pure FreeRADIUS

`aaa-service` handles NAS/RADIUS subscriber auth (PAP/CHAP/EAP), NAS onboarding,
IPAM, and network control. It does **not** issue application-user tokens and does
not own application user records. See `docs/apis/milestone-0-aaa-nas-radius.md`.
