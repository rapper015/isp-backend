# Subscriber CSV import

## Architecture and gap analysis

The original backend had `Customer`, `Subscriber`, `Plan`, `NasDevice`, and billing records, but no tenant, location masters, import audit trail, document availability fields, or background worker. The implementation reuses those domain records, adds `Franchise`, `Branch`, `Area`, and typed `NetworkLocation` masters, and adds nullable source/network/subscription fields. Existing API fields and legacy rows remain valid. `SubscriberImportBatch` owns the private upload and aggregate state; `SubscriberImportRow` owns positional raw data, normalized data, decisions, errors, and the target record.

There is no configured Celery installation, so validation and commit are synchronous service calls. Commit processes 250-row chunks with an outer transaction and a savepoint per row, allowing a failed row to roll back without losing successful rows in the chunk.

## Migrations

- `customers/0003`: franchise/location masters and import-backed customer fields.
- `plans/0003`: optional franchise scope and raw package labels.
- `network/0003` and `0004`: scoped network locations and tenant-aware NAS uniqueness.
- `subscribers/0003` and `0004`: import audit tables, subscriber import fields, and conditional tenant-aware external-ID, username, MAC, and IP constraints.

All new foreign keys on existing operational rows are nullable. Legacy subscribers with no franchise retain conditional global username uniqueness.

## Field mapping

| CSV | Target |
|---|---|
| Id | `Customer.external_id`, `Subscriber.external_id` |
| CAF No | `Customer.caf_number` |
| Status | `Customer.status`, `Subscriber.status` (`expired` becomes inactive; raw value retained) |
| Outage / Account Type | `Subscriber.outage_enabled`, `account_type` |
| Franchise Name | selected `Franchise` (must match) |
| Username | `Subscriber.username` |
| MAC / Allowed MACs / IpAddress | `Subscriber.mac_address`, `allowed_macs`, `static_ip_address` |
| Name / Father/Company Name | `Customer.full_name`, `father_or_company_name` |
| Mobile / Alt. Mobile / Email / GSTIN | customer contact fields |
| Package Name | `Plan`; also `Subscriber.source_package_name` |
| Sub Package | `Subscriber.source_sub_package` and auto-created plan source label |
| Expiry Date / Last Renewal / FUP Limit | subscriber subscription fields |
| Package Price / Custom Price / Spl. Discount / Add. Charges | subscriber decimal fields |
| First Wallet Balance | authoritative `Subscriber.current_balance` |
| Second Wallet Balance | `Subscriber.import_metadata.wallet_balance_secondary` |
| Balance Amount / Last Payment Source | subscriber billing fields |
| Branch / Area | scoped customer master relations |
| Colony / Building / City / State / Door No | customer address fields |
| Billing/Installation Address | customer text fields; installation also maps to subscriber |
| Latitude / Longitude | customer decimal coordinates |
| Node / Pop / Switch | typed `NetworkLocation` relations |
| NAS IP | scoped `NasDevice`; blank never creates a NAS |
| Nas Port Id / Last Logoff | subscriber network fields |
| POP Tech Exe / POP Coll Exe | subscriber assignment text |
| CAF Form / Address Proof / Identity Proof / Customer Pic | nullable customer availability booleans |
| Date Added / Commitment Date / User Added | customer source audit fields |
| Auto Renew / Connection Type | subscriber fields |

The complete original positional row is also retained in `Subscriber.import_metadata.raw_source`; unmapped values are never discarded.

## API

Only a JWT admin with role `super_admin` may use these routes. A franchise must exist before upload.

```http
POST /api/v1/subscriber-imports/validate/
Authorization: Bearer <token>
Content-Type: multipart/form-data

file=@users.csv
franchise_id=1
update_existing=true
create_missing_packages=false
create_missing_locations=false
dry_run=true
```

```json
{
  "success": true,
  "import_id": "07be93e8-f228-40a2-88c9-26c53497b860",
  "status": "VALIDATED",
  "summary": {"total_rows": 2810, "valid_rows": 2800, "invalid_rows": 10, "create_count": 2700, "update_count": 100, "skip_count": 0},
  "warnings": [],
  "sample_rows": [],
  "error_download_url": "/api/v1/subscriber-imports/07be93e8-f228-40a2-88c9-26c53497b860/errors/download/"
}
```

Commit with `POST /api/v1/subscriber-imports/{id}/commit/`; options in the JSON body override preview options. Status and history use `GET /api/v1/subscriber-imports/{id}/` and `GET /api/v1/subscriber-imports/`. History supports `page`, `page_size`, `franchise_id`/`tenant_id`, `status`, `date_from`, and `date_to`. Rows use `GET .../{id}/rows/?action=ERROR`; error CSV uses `GET .../{id}/errors/download/`; failed rows retry with `POST .../{id}/retry/`; an in-flight synchronous batch may be cancelled through `POST .../{id}/cancel/` when control returns between operations.

## Validation and upsert rules

The parser accepts UTF-8/BOM, quoted commas and multiline fields, and treats headers positionally. Only the two Wallet Balance headers may be duplicated. Blank strings become null during normalization. Identifiers remain strings. Boolean, date, `Decimal`, Indian mobile, MAC, IP, and coordinate validation produces field errors. Enum values are explicitly mapped; unknown values fail and remain in raw data.

Identity priority is source external ID, username, MAC, then IP, all within the selected franchise. Any disagreement is an identity conflict. Existing records update only when enabled, and blank source fields never replace populated target values. A second file with the same hash warns; database identities ensure repeat imports cannot create duplicates. Location matching is trimmed and case-insensitive but never fuzzy.

## Configuration and operation

`SUBSCRIBER_IMPORT_MAX_BYTES` defaults to 10 MiB. `MEDIA_ROOT/private_media` storage is git-ignored and is not routed publicly. Production deployments should configure a private storage backend and normal log collection. No Celery configuration is required.

Run:

```powershell
py -3.12 manage.py migrate
py -3.12 manage.py runserver 0.0.0.0:4000
py -3.12 manage.py test subscribers
```

## Business confirmations still required

- The first Wallet Balance is treated as authoritative because its source meaning is otherwise undocumented; the second is preserved, never summed.
- `expired` maps to the existing `inactive` status because the operational enum has no expired state.
- The CSV contains no subscriber password. New imports get a deterministic non-usable placeholder hash and cannot password-authenticate until credentials are provisioned.
- Auto-created packages use the CSV package price, a minimal placeholder speed profile, and retain package/sub-package labels. Operations should complete speed and billing-cycle data before activation.
- The source uses connection type `0`; it is preserved as a known source value rather than guessed as Fiber or Cat5.
