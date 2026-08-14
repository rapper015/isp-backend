# Invoice CSV import

The invoice importer mirrors the subscriber import workflow and keeps a complete audit trail. It resolves every row inside the selected franchise, using `A/C No`, `Username`, and `IpAddress`; if those identifiers point at different subscribers, the row is rejected. The resolved subscriber supplies the customer relationship. `Package Name` resolves the invoice plan, with optional inactive-plan creation.

## API

All endpoints require a super-admin JWT.

- `POST /api/v1/invoice-imports/validate/` — multipart fields: `file`, `franchise_id` (or `tenant_id`), `update_existing`, `create_missing_packages`, and `dry_run`.
- `POST /api/v1/invoice-imports/{id}/commit/` — commits validated rows; the two mutation options may be overridden in JSON.
- `GET /api/v1/invoice-imports/` and `GET /api/v1/invoice-imports/{id}/` — history and detail.
- `GET /api/v1/invoice-imports/{id}/rows/`, optionally filtered by `action`.
- `GET /api/v1/invoice-imports/{id}/errors/download/` — formula-safe error CSV.
- `POST /api/v1/invoice-imports/{id}/retry/` and `POST /api/v1/invoice-imports/{id}/cancel/`.

Commit creates an `Invoice` and its invoice `LedgerEntry`. A positive `Paid Amount` also creates a `Payment` and payment `LedgerEntry`. These writes occur atomically per source row. Re-import is idempotent by subscriber, source system, and source invoice number. Imported invoice numbers are stored separately and the internal number is namespaced as `LEGACY-{franchise_id}-{Invoice No}` to avoid cross-franchise collisions.

The importer deliberately does not add historical invoice totals to `Subscriber.current_balance` or `BillingAccount.outstanding_balance`: the subscriber export already supplies the current balance snapshot, and replaying historical invoices would double-count it. All source columns remain available in the row audit record, and invoice-specific source context is retained in `Invoice.import_metadata`.
