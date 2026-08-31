# Authentication architecture audit

Audit date: 2026-08-31. Scope: `milestone-0` through `milestone-10`, service
source, gateway, Compose, migration files, environment examples and Git
ancestry.

## Current state and findings

The milestone branches form a cumulative ancestry chain before this change.
`identity-service` is, however, a separately deployed service in the M0
Compose stack with a separate `identity` database and public gateway route.
It auto-creates tables during application startup and its Alembic revision
also delegates schema generation to ORM metadata. It has one `role` column and
hard-coded role-to-permission maps.

CRM and AAA each independently decode an HMAC JWT, read a service-specific
`*_JWT_SECRET`, and expand their own `ROLE_PERMISSIONS` maps. This produces
multiple authorization authorities. CRM additionally names an unrelated
`CRM_JWT_SECRET`; AAA names `AAA_JWT_SECRET`. The gateway comments describe
Identity as a dedicated IAM service. The root Compose publishes AAA directly
and contains development credentials as effective defaults.

AAA owns subscriber credentials, NAS secrets, accounting and RADIUS policy.
Those records are separate from Identity's `idp_users`, but the existing route
and service names make the ownership boundary unnecessarily easy to misuse.
No evidence of an automatic identity-data migration was found. The existing
`identity` database must therefore be treated as potentially populated and
must not be deleted.

## Security risks

* Runtime schema creation makes production schema state non-deterministic.
* Long-lived, non-rotating access tokens are the only session mechanism.
* Refresh-token revocation, account lockout and password-change invalidation
  are absent.
* Independent per-service role maps allow authorization drift.
* JWT issuer and token type are not consistently validated.
* Shared Compose defaults are unsafe for production and AAA exposes a host
  port in the full platform stack.

## Target architecture and migration

`platform-core-service` is the M0 platform runtime and owns platform users,
roles, permissions, memberships, refresh tokens, service accounts and security
audit events in a `platform_core` database. It is the only issuer of platform
access tokens. It uses Argon2id password hashes and hashed, rotating refresh
tokens. AAA remains the owner of subscriber/RADIUS data in `aaa`; CRM remains
the owner of customer lifecycle data in `crm`.

CRM and AAA management APIs validate the issuer's token contract and enforce
permissions from claims; their local role expansions are removed. RADIUS and
internal calls retain their narrowly-scoped service credentials and are never
accepted by platform-auth routes.

The old Identity database is not dropped. `docs/migrations/identity-to-platform-auth.md`
documents a count-verified, idempotent copy that preserves UUIDs and compatible
password hashes, role and enabled state. The source database remains available
for rollback.

The M0 commit will be made first, then each milestone branch will be replayed
sequentially on it. Backup branches named `backup/milestone-*-20260831-190804`
preserve every pre-repair tip. No remote branch will be pushed or deleted.

## Files and branches changed

M0 changes the root Compose, gateway, PostgreSQL database bootstrap list,
environment template, AAA source/tests, introduces `platform-core-service`,
and removes the deployed `identity-service`. Documentation is added under
`docs/architecture`, `docs/deployment` and `docs/migrations`. M1 adds the CRM
consumer contract when replayed; later milestones inherit the same M0 base.
