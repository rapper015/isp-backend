# Cumulative Milestone Model

The platform is delivered in milestones **0–10**. Every milestone branch is
**cumulative**: milestone-N contains *everything* from milestones 0..N, plus
that milestone's own deliverables.

> Rule of thumb: if a feature is in milestone-0 it is also in milestone-1,
> milestone-2, … milestone-10. If a feature is in milestone-3 it is also in
> milestone-4, … milestone-10. Later milestones are always supersets.

## Branch topology

The milestone branches form a **linear chain** (each is an ancestor of the next):

```
milestone-0 ─▶ milestone-1 ─▶ milestone-2 ─▶ … ─▶ milestone-10
```

```
ae6842d (full platform, pre-chain) ─▶ milestone-0 ─▶ milestone-1 ─▶ … ─▶ milestone-10
```

Because the chain is linear, git itself guarantees cumulation: the tree of
milestone-N is a strict subset of the tree of milestone-(N+1).

## Service → milestone mapping

| Milestone | Services added | DB added |
|-----------|----------------|----------|
| 0 | `aaa-service` (FreeRADIUS/NAS), `identity-service` (app-user auth) | aaa, identity |
| 1 | `crm-service` | crm |
| 2 | `oss-service` | oss |
| 3 | `nms-service` | nms |
| 4 | `bss-service` | bss |
| 5 | `support-service` (dir only; not wired into compose) | – |
| 6 | `workforce-service` | workforce |
| 7 | `device-management-service` | device_management |
| 8 | `tenancy-service` | tenancy |
| 9 | `assurance-service` | assurance |
| 10 | `intelligence-service`, `siem-service`, `warehouse-service`, `aiops-service`, `ipam-service` | intelligence, siem, warehouse, aiops, ipam |

Plus `aaa-worker` (m0), `workforce-worker` (m6), `device-management-worker` (m7),
`tenancy-worker` (m8), `assurance-worker` (m9), `intelligence-worker` / `siem-worker` (m10).

## Platform foundation (present on every branch)

`infrastructure/`, `shared/`, `docs/`, `.env.example`, `README.md`, `.gitignore`,
`services/_template/`, and the per-milestone `docker-compose.yml` + gateway
`nginx.conf`.

## Wiring is pruned per milestone

`docker-compose.yml` and `infrastructure/gateway/nginx.conf` on each branch are
pruned to reference **only the services present in that milestone** (plus the
shared infra: gateway, postgres, rabbitmq, valkey, observability stack). This
keeps every milestone branch self-consistent and `docker compose config`-valid.

- Gateway `depends_on` lists only active services.
- `POSTGRES_MULTIPLE_DATABASES` lists only active databases.
- nginx defines upstreams/locations only for active services.

## Identity auth is milestone-0

`identity-service` (application-user auth: create users + login → JWT) belongs
to **milestone-0** and therefore exists on every later milestone branch too.
It is deliberately separate from `aaa-service`, which remains pure
FreeRADIUS/NAS subscriber auth. See `docs/identity-auth.md`.

## Verify cumulation

```bash
# The tree of every earlier milestone must be a subset of milestone-10:
for m in $(seq 0 9); do
  git diff --stat milestone-$m milestone-10   # should only ADD files, never remove milestone-$m content
done

# Each branch is a direct descendant of the previous:
git log --oneline milestone-10   # linear: 0 → 1 → … → 10
```
