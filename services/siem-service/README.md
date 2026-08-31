# SIEM Service

Security, compliance, and case-management foundation — **Master Implementation
Spec Batch 1**. `sec_` tables, `siem.events.v1` contracts, fail-closed tenant
isolation, append-only audit, tamper-evident evidence.

## Capabilities (feature → implementation)

| Feature | Capability | Where |
|---|---|---|
| 407, 408, 448 | Central tamper-proof log repo, hash-chained evidence, bulk ingest | `app/services.py:EventService`, `POST /api/siem/v1/internal/ingest/events` |
| 417, 418 | Field encryption + PII masking | `app/crypto.py` |
| 401, 426, 441, 442, 450, 1371 | Policy definition/enforcement, violation detection, continuous scan | `app/services.py:PolicyService`, `app/tasks.py:rescan_violations` |
| 404, 405, 406, 1334 | Retention policies, archive/purge sweeps | `app/services.py:RetentionService`, `app/tasks.py:sweep_retention` |
| 421, 422, 423 | Consent management, DSAR, right to erasure | `app/services.py:ConsentService`, `DsarService` |
| 1414, 1415, 1471–1474 | Case lifecycle, escalation matrix, impact analysis, breach notify | `app/services.py:CaseService` |
| 411–416 | Lawful interception request/authorization | `app/services.py:LiService` |
| 1173–1175 | Vulnerability ingest/remediation | `app/services.py:VulnerabilityService` |
| 420, 438, 439, 440, 1163 | Append-only audit trail, search, export | `app/routing.py:record_audit`, API `/api/siem/v1/audit-log` |
| 425, 428, 1234, 1475 | Compliance/SOC dashboard + regulatory reports | `GET /api/siem/v1/dashboard/summary`, `POST /api/siem/v1/regulatory/reports` |

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\..\shared\runtime\requirements.txt -r requirements.txt
set DATABASE_URL=sqlite:///./siem.db
set SIEM_JWT_SECRET=<32+ chars>
set SIEM_INTERNAL_API_KEY=<internal key>
set SIEM_ENCRYPTION_KEY=<32+ chars>
uvicorn app.main:app --port 8012
python -m app.worker_runner
```

## Tests

```bash
python -m pytest
```
