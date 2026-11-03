# ISP Platform microservice foundation

The repository is now a microservice monorepo. It includes independently
deployable CRM, BSS, OSS, AAA, NMS, IPAM, SIEM, Workforce, Warehouse, and
AIOps service foundations, plus the existing backend at
[`services/core-platform`](services/core-platform) for incremental migration.

See [`docs/architecture.md`](docs/architecture.md) for migration rules and
[`services/README.md`](services/README.md) for service boundaries.

## Run the existing backend locally

```bash
cd services/core-platform
python -m venv venv
venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py runserver 0.0.0.0:4000
```

## Run the platform containers

```bash
copy services\\core-platform\\.env.example services\\core-platform\\.env
docker compose up --build
docker compose exec core-platform python manage.py migrate
```

For a pre-existing local SQLite setup, move the ignored `db.sqlite3` file into
`services/core-platform` first.

## Redeploy changed services

`docker compose up -d SERVICE` reuses an existing image. It does not copy
newly pulled source code into that image. After pulling a commit that changes a
service or its migrations, rebuild and force-recreate the affected containers:

```bash
sh scripts/redeploy-services.sh aaa-service aaa-worker
```

The script preserves database volumes. To perform the same steps manually:

```bash
docker compose build --pull aaa-service aaa-worker
docker compose up -d --force-recreate aaa-service aaa-worker
docker compose logs --no-color --tail=100 aaa-service
```
