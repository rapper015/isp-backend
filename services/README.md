# Services

This is a monorepo using a strangler migration strategy. Every domain service
below is independently deployable today and has a health endpoint, its own
logical PostgreSQL database, RabbitMQ and Valkey access, and a gateway route.
`core-platform` is the existing Django application, preserved while business
capabilities are extracted incrementally.

| Service boundary | Initial owner | Extraction status |
| --- | --- | --- |
| CRM | customer, lead, KYC, lifecycle, reseller apps | `crm-service` foundation |
| BSS | billing, payments, plans apps | `bss-service` foundation |
| OSS | orders, subscriber provisioning, resources apps | `oss-service` foundation |
| AAA | aaa and network access-control apps | `aaa-service` foundation |
| NMS | NAS health and network monitoring | `nms-service` foundation |
| IPAM | IP/VLAN/resource allocation | `ipam-service` foundation |
| SIEM | security audit/event ingestion | `siem-service` foundation |
| Workforce | field operations | `workforce-service` foundation |
| Data Warehouse | analytical projections | `warehouse-service` foundation |
| AIOps | predictive automation | `aiops-service` foundation |
| Device Management | TR-069 CPE control plane (identity, profiles, verified config, drift, diagnostics, firmware rollouts via GenieACS) | `device-management-service` (Milestone 7) |
| Tenancy | franchise & multi-tenant management (tenant registry/lifecycle, config/branding, org hierarchy, partners, scoped RBAC + SoD, commissions, settlements, wallets, tenant-aware reports) | `tenancy-service` (Milestone 8) |
| Assurance | observability & service assurance (service catalogue, SLI/SLO/error budgets, alert lifecycle, incidents, root cause, postmortems, KPIs, maintenance windows, synthetic checks, reports; governance layer only — raw telemetry lives in Prometheus/Loki/Tempo via OTel Collector) | `assurance-service` (Milestone 9) |
| Intelligence | AI & intelligence layer (governed ingestion/data contracts/quality, feature store, MLOps registry + deploy/monitor lifecycle, fraud, churn, predictive maintenance, capacity forecasting, recommendations, remediation intents with autonomy levels + approval + kill switch; never mutates domain state directly) | `intelligence-service` (Milestone 10) |

New services must be created from `_template`, own their database, expose
`/health`, and communicate with other services only through versioned HTTP or
event contracts under `shared/contracts`.

The foundation endpoint for a service is available through the gateway, for
example `GET /api/v1/crm/status`. It proves the routing and deployment path;
it is not yet a replacement for the related `core-platform` business API.

CRM is the first extraction in progress. Its customer API is served at
`/api/v1/crm/customers`; it owns the `crm` database and uses UUID identifiers.
