# Platform authentication

M0 `platform-core-service` owns platform users, roles, permissions, sessions,
refresh tokens and security audit events in `platform_core`. It is the sole
issuer of `iss=isp-platform` access tokens. Passwords use Argon2id; refresh
tokens are random, hashed at rest, rotated on use and revoked on logout or
password change. Services verify issuer, expiry, token type and permissions.

Run `alembic upgrade head` before starting the service. Runtime schema creation
is intentionally disabled. Rotate `PLATFORM_JWT_SECRET` through a planned
dual-verifier deployment; do not place it in source control.
