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
