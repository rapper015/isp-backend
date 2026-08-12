# ISP Management Platform (Django)

Milestone 0 of the Telecom ISP Management Platform, implemented in Django +
Django REST Framework + PostgreSQL:

- **0.1** AAA Authentication Core
- **0.2** Session Accounting Engine
- **0.3** Basic Billing Engine
- **0.4** Invoice and Ledger System
- **0.5** Minimal Customer Mapping (satisfied by the `Subscriber` → `Customer`/`Plan` relations)

This is a port of the reference Node/Express/MongoDB implementation
(`isp-express-main`), preserving the exact `/internal/aaa/*` request/response
contract so the existing FreeRADIUS `rlm_rest` configuration works unchanged
against this service on the same port (4000), and replicating the
`/api/v1/auth`, `/api/v1/billing/*`, `/api/v1/payments` business logic
(charge calculation, invoice numbering, ledger postings, payment recording).

## Stack

- Django + Django REST Framework
- PostgreSQL
- Django admin for managing customers / plans / subscribers / NAS devices / admin users / billing settings
- `bcrypt` for password hashing (subscriber RADIUS/PAP passwords and admin login passwords)
- `PyJWT` for stateless admin session tokens
- `Decimal`-based money math throughout (fixes a float-rounding bug present in the Node reference's payment recording)

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# edit .env: set DATABASE_URL to your Postgres instance, INTERNAL_API_KEY,
# JWT_SECRET, DEFAULT_ADMIN_EMAIL/PASSWORD, etc.

python manage.py migrate
python manage.py ensure_default_admin   # idempotent, creates the default super_admin
python manage.py createsuperuser        # optional, for a separate Django-admin-site login
python manage.py runserver 0.0.0.0:4000
```

Use `/admin/` to create at least one `Plan`, one `Customer`, and one
`Subscriber` (the Subscriber admin form takes a plaintext password and
bcrypt-hashes it into `password_hash`).

## Internal AAA API (used by FreeRADIUS `rlm_rest`)

All routes require header `x-internal-api-key: <INTERNAL_API_KEY>`.

- `POST /internal/aaa/authenticate` — validates credentials + eligibility, returns `Access-Accept` / `Access-Reject`.
- `POST /internal/aaa/authorize` — returns MikroTik rate-limit / pool / VLAN attributes for an eligible subscriber.
- `POST /internal/aaa/accounting` — ingests RADIUS accounting events (`start`/`interim`/`stop`), maintains `ActiveSession` + `AccountingRecord` + `NasDevice` state.
- `POST /internal/aaa/post-auth`, `/disconnect`, `/coa` — acknowledge/log post-auth, disconnect, and CoA events.

Every request accepts either the platform's own camelCase field names or raw
RADIUS attribute names (`User-Name`, `Acct-Session-Id`, `Acct-Status-Type`,
etc.), matching the `rlm_rest` payload templates already deployed in
`docs/freeradius-isp-platform-rest.conf` on the Node project.

`GET /health` returns a basic liveness check.

## Admin business API (`/api/v1/*`)

`POST /api/v1/auth/login` with `{"email", "password"}` returns
`{"token", "admin": {...}}`. Send `Authorization: Bearer <token>` on every
other `/api/v1/*` call. Any authenticated admin (regardless of role) can
access all billing/payment endpoints — this matches the Node reference, whose
role-gating middleware exists but is never actually applied to these routes.

- `GET/POST /api/v1/billing/invoices`, `GET/PATCH /api/v1/billing/invoices/<id>`
- `POST /api/v1/billing/invoices/generate-due` — monthly batch invoicing run
- `POST /api/v1/billing/invoices/refresh-overdue` — flips overdue invoices, auto-suspends per settings
- `GET /api/v1/billing/ledger`
- `POST /api/v1/billing/adjustments` — manual debit/credit against an invoice/account/subscriber
- `GET/PATCH /api/v1/billing/settings`
- `GET /api/v1/billing/accounts`, `PATCH /api/v1/billing/accounts/<id>/plan`
- `GET/POST /api/v1/payments/` (note the trailing slash)

Invoice/payment numbers (`INV-YYYYMM-NNNNNN` / `PAY-YYYYMM-NNNNNN`) are
generated from an atomic `SequenceCounter` table (`billing/sequences.py`),
fixing a collision risk in the Node reference's epoch-millis-suffix scheme.

## Not in scope yet

MikroTik NAS onboarding and management is documented in
[`docs/mikrotik-nas.md`](docs/mikrotik-nas.md).

Full CRM (Milestone 1), provisioning/orchestration (Milestone 2), and
everything after are later milestones; use the Django admin for supporting
data entry in the meantime.
