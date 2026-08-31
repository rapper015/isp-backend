# Identity to platform-auth migration

This is a copy migration; it never drops the legacy `identity` database.

1. Back up the `identity` and `platform_core` databases and record `idp_users`
   and `platform_users` counts.
2. Apply platform-core Alembic migrations.
3. In a transaction, copy compatible `idp_users` UUID, username, email,
   password hash, tenant, enabled/status and timestamps to `platform_users`.
   Map legacy roles to seeded platform roles. Use `ON CONFLICT (id) DO NOTHING`
   so reruns are idempotent.
4. Compare counts and sample UUID/hash values, then switch gateway traffic.

Argon2id hashes copy directly. Incompatible legacy password hashes require a
password-reset campaign; do not weaken verification. Roll back by restoring the
gateway route and using the retained Identity database; do not delete either
volume during the cutover.
