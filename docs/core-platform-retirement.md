# Core-platform retirement checklist

`core-platform` must not be deleted until every legacy route below has an
equivalent service implementation, authenticated gateway route, data export,
and end-to-end test. The new services are foundations, not yet replacements
for these complete contracts.

| Legacy capability | Destination | Current status |
| --- | --- | --- |
| `/api/v1/customers`, leads, KYC, lifecycle, franchises, branches | CRM | partial: new aggregates and APIs exist; route/data/auth parity missing |
| `/api/v1/plans`, billing, payments, invoice imports | BSS | partial: plans/invoices/payments exist; ledger, settings, imports, route parity missing |
| `/api/v1/subscribers`, orders, subscriber imports | OSS | partial: subscribers/orders exist; provisioning/import parity missing |
| `/internal/aaa/*` | AAA | partial: credential authentication exists; full RADIUS contract missing |
| `/api/v1/auth/*` | Identity (AAA boundary) | not started: admin identity, JWT issuing, and authorization claims remain in Django |
| `/api/v1/network/*`, `/api/v1/nas/*` | NMS | partial: NAS inventory/observations exist; RouterOS management missing |
| resource and allocation routes | IPAM | partial: pool/address allocation exists; VLAN and release flow missing |
| dashboard APIs | Warehouse | not started |
| security events | SIEM | not started |
| field operations | Workforce | not started |
| prediction/automation | AIOps | not started |

## Removal gate

Before deleting the legacy folder, run a contract test for every old public
route against its destination service, validate imported record counts and
checksums, stop gateway forwarding to `core-platform`, and run an end-to-end
RADIUS authentication and billing-payment workflow. A passing health endpoint
alone is not evidence of replacement parity.
