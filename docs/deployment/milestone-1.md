# Milestone 1 deployment and CRM-only upgrade

Preserve the existing CRM PostgreSQL volume. Before cutover, back it up and
record CRM table counts. Use the canonical root Compose file (not an untracked
`docker-compose.crm.yml`), apply platform-core, AAA and CRM migrations, then
start the stack. Do not create a second stack or bind application port 8000.

Verify CRM counts before/after, `/health`, platform login and an authenticated
CRM endpoint. Roll back gateway/application images to the CRM-only stack while
retaining the original volume; restore the backup only after confirming the
rollback path needs data restoration.
