import hashlib
import logging
import secrets
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from common.passwords import hash_password
from customers.models import Area, Branch, Customer, Franchise
from network.models import NasDevice, NetworkLocation
from plans.models import Plan
from subscribers.models import Subscriber, SubscriberImportBatch, SubscriberImportRow

from .parser import CSVStructureError, normalize_row, read_csv

logger = logging.getLogger(__name__)
CHUNK_SIZE = 250


def file_digest(upload):
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def find_identity(data, franchise):
    candidates = set()
    external_id = data.get("external_id")
    if external_id:
        candidates.update(Subscriber.objects.filter(franchise=franchise, source_system=data["source_system"], external_id=external_id, deleted_at__isnull=True).values_list("id", flat=True))
    username = data.get("username")
    if username:
        candidates.update(Subscriber.objects.filter(franchise=franchise, username__iexact=username, deleted_at__isnull=True).values_list("id", flat=True))
    mac = data.get("mac_address")
    if mac:
        candidates.update(Subscriber.objects.filter(franchise=franchise, mac_address__iexact=mac, deleted_at__isnull=True).values_list("id", flat=True))
    ip = data.get("ip_address")
    if ip:
        candidates.update(Subscriber.objects.filter(franchise=franchise, static_ip_address=ip, deleted_at__isnull=True).values_list("id", flat=True))
    if len(candidates) > 1:
        return None, "Identifiers match different subscribers"
    return (Subscriber.objects.filter(id=next(iter(candidates))).first() if candidates else None), None


def validate_upload(upload, franchise, created_by, options):
    batch = SubscriberImportBatch.objects.create(
        franchise=franchise, original_filename=upload.name, file_hash=file_digest(upload), file=upload,
        status=SubscriberImportBatch.Status.VALIDATING, options=options, created_by=created_by, started_at=timezone.now(),
    )
    try:
        with batch.file.open("rb") as stored:
            headers, keys, parsed_rows = read_csv(stored)
        row_models, counts = [], {"create_count": 0, "update_count": 0, "skip_count": 0, "duplicate_count": 0}
        seen = set()
        existing_rows = list(Subscriber.objects.filter(franchise=franchise, deleted_at__isnull=True).only("id", "external_id", "source_system", "username", "mac_address", "static_ip_address"))
        identity_maps = {
            "external": {(item.source_system, item.external_id): item for item in existing_rows if item.external_id},
            "username": {item.username.casefold(): item for item in existing_rows},
            "mac": {item.mac_address.casefold(): item for item in existing_rows if item.mac_address},
            "ip": {item.static_ip_address: item for item in existing_rows if item.static_ip_address},
        }
        plan_names = {name.casefold() for name in Plan.objects.filter(Q(franchise=franchise) | Q(franchise__isnull=True), deleted_at__isnull=True).values_list("name", flat=True)}
        missing_master_warnings = set()
        for row_number, raw, structural_errors in parsed_rows:
            data, errors = normalize_row(raw) if raw else ({}, structural_errors)
            if structural_errors and raw:
                errors.extend(structural_errors)
            if data.get("franchise_name") and data["franchise_name"].casefold() != franchise.name.casefold():
                errors.append({"field": "franchise_name", "value": data["franchise_name"], "error": "Row franchise does not match the selected franchise"})
            identity_key = (data.get("external_id"), (data.get("username") or "").casefold())
            if identity_key in seen:
                errors.append({"field": "row", "value": str(identity_key), "error": "Duplicate identity within file"})
                counts["duplicate_count"] += 1
            seen.add(identity_key)
            matched = {
                identity_maps["external"].get((data.get("source_system"), data.get("external_id"))),
                identity_maps["username"].get((data.get("username") or "").casefold()),
                identity_maps["mac"].get((data.get("mac_address") or "").casefold()),
                identity_maps["ip"].get(data.get("ip_address")),
            } - {None}
            existing = next(iter(matched)) if len(matched) == 1 else None
            if len(matched) > 1:
                errors.append({"field": "identity", "value": "", "error": "Identifiers match different subscribers"})
            if (data.get("package_name") or "").casefold() not in plan_names and not options.get("create_missing_packages"):
                errors.append({"field": "package_name", "value": data.get("package_name") or "", "error": "Package does not exist"})
            if not options.get("create_missing_locations"):
                for field in ("branch", "area", "node", "pop", "switch", "nas_ip"):
                    if data.get(field):
                        missing_master_warnings.add(f"Missing {field} values are not created unless create_missing_locations=true")
            action = SubscriberImportRow.Action.ERROR if errors else (SubscriberImportRow.Action.UPDATE if existing and options.get("update_existing") else SubscriberImportRow.Action.SKIP if existing else SubscriberImportRow.Action.CREATE)
            if action == "CREATE": counts["create_count"] += 1
            elif action == "UPDATE": counts["update_count"] += 1
            elif action == "SKIP": counts["skip_count"] += 1
            row_models.append(SubscriberImportRow(import_batch=batch, source_row_number=row_number, external_id=data.get("external_id") or "", username=data.get("username") or "", raw_data=raw, normalized_data=data, action=action, validation_errors=errors, target_subscriber=existing))
        SubscriberImportRow.objects.bulk_create(row_models, batch_size=CHUNK_SIZE)
        invalid = sum(1 for row in row_models if row.action == "ERROR")
        batch.total_rows = len(row_models); batch.invalid_rows = invalid; batch.valid_rows = len(row_models) - invalid; batch.duplicate_rows = counts["duplicate_count"]
        batch.column_mapping = {str(i): {"source": h, "target": keys[i]} for i, h in enumerate(headers)}
        duplicate_file = SubscriberImportBatch.objects.filter(franchise=franchise, file_hash=batch.file_hash, status__in=["COMPLETED", "PARTIAL"]).exclude(id=batch.id).exists()
        warnings = sorted(missing_master_warnings)
        if duplicate_file: warnings.append("This file hash was imported previously")
        batch.validation_summary = {**counts, "warnings": warnings}
        batch.status = SubscriberImportBatch.Status.VALIDATED; batch.completed_at = timezone.now(); batch.save()
        logger.info("Subscriber import validated", extra={"import_id": str(batch.id), "admin_id": created_by.id, "rows": batch.total_rows})
        return batch
    except Exception:
        batch.status = SubscriberImportBatch.Status.FAILED; batch.completed_at = timezone.now(); batch.save(update_fields=["status", "completed_at"])
        raise


def _master(model, franchise, name, create, **extra):
    if not name: return None
    query = model.objects.filter(franchise=franchise, normalized_name=name.strip().casefold(), **extra)
    obj = query.first()
    if obj or not create: return obj
    return model.objects.create(franchise=franchise, name=name.strip(), normalized_name=name.strip().casefold(), **extra)


def _plan(data, franchise, create):
    plan = Plan.objects.filter(Q(franchise=franchise) | Q(franchise__isnull=True), name__iexact=data["package_name"], deleted_at__isnull=True).first()
    if plan or not create: return plan
    code = hashlib.sha1(f"{franchise.id}:{data['package_name']}".encode()).hexdigest()[:16].upper()
    return Plan.objects.create(franchise=franchise, plan_code=f"IMP-{code}", name=data["package_name"], source_name=data["package_name"], source_sub_package=data.get("sub_package") or "", description="Created by subscriber CSV import", monthly_fee=Decimal(data.get("package_price") or "0"), speed_profile_name="imported-unmapped", download_rate_kbps=1, upload_rate_kbps=1, status=Plan.Status.INACTIVE)


def _set_nonblank(obj, mapping, data):
    for field, source in mapping.items():
        value = data.get(source)
        if value is not None and value != "" and value != []:
            setattr(obj, field, value)


def process_row(row, options):
    data, franchise = row.normalized_data, row.import_batch.franchise
    existing, conflict = find_identity(data, franchise)
    if conflict: raise ValueError(conflict)
    if existing and not options.get("update_existing"):
        row.action = "SKIP"; row.target_subscriber = existing; return "skipped"
    plan = _plan(data, franchise, options.get("create_missing_packages"))
    if not plan: raise ValueError("Package does not exist")
    create_locations = options.get("create_missing_locations", False)
    branch = _master(Branch, franchise, data.get("branch"), create_locations)
    area = _master(Area, franchise, data.get("area"), create_locations)
    locations = {kind: _master(NetworkLocation, franchise, data.get(kind), create_locations, kind=kind) for kind in ("node", "pop", "switch")}
    nas = None
    if data.get("nas_ip"):
        nas = NasDevice.objects.filter(franchise=franchise, nas_ip_address=data["nas_ip"], deleted_at__isnull=True).first()
        if not nas and create_locations:
            nas = NasDevice.objects.create(franchise=franchise, nas_ip_address=data["nas_ip"], name=data["nas_ip"])
    customer = existing.customer if existing else Customer.objects.create(customer_code=f"IMP-C-{franchise.id}-{data['external_id']}"[:64], full_name=data["full_name"], phone=data.get("primary_mobile") or "", email=data.get("email") or "", address=data.get("billing_address") or data.get("installation_address") or "", city=data.get("city") or "", status=data.get("status") or "active", franchise=franchise, external_id=data["external_id"], source_system=data["source_system"])
    _set_nonblank(customer, {"full_name":"full_name", "phone":"primary_mobile", "alternate_phone":"alternate_mobile", "email":"email", "caf_number":"caf_number", "father_or_company_name":"father_or_company_name", "gstin":"gstin", "colony":"colony", "building":"building", "city":"city", "state":"state", "door_number":"door_number", "billing_address":"billing_address", "installation_address":"installation_address", "latitude":"latitude", "longitude":"longitude", "source_added_at":"source_added_at", "commitment_date":"commitment_date", "source_created_by":"source_created_by", "caf_form_available":"caf_form_available", "address_proof_available":"address_proof_available", "identity_proof_available":"identity_proof_available", "customer_picture_available":"customer_picture_available"}, data)
    customer.branch = branch or customer.branch; customer.area = area or customer.area
    customer.import_metadata = {**customer.import_metadata, "last_import_id": str(row.import_batch_id), "raw_unmapped": {k:v for k,v in row.raw_data.items() if k.startswith("unmapped__")}}
    customer.save()
    subscriber = existing or Subscriber(subscriber_code=f"IMP-S-{franchise.id}-{data['external_id']}"[:64], customer=customer, franchise=franchise, username=data["username"], password_hash=hash_password(secrets.token_urlsafe(48)), plan=plan, installation_address=data.get("installation_address") or data.get("billing_address") or "", external_id=data["external_id"], source_system=data["source_system"])
    _set_nonblank(subscriber, {"username":"username", "status":"status", "outage_enabled":"outage_enabled", "account_type":"account_type", "connection_type":"connection_type", "auto_renew":"auto_renew", "mac_address":"mac_address", "allowed_macs":"allowed_macs", "static_ip_address":"ip_address", "nas_port_id":"nas_port_id", "expires_at":"expiry_at", "last_logoff_at":"last_logoff_at", "last_renewal_at":"last_renewal_at", "fup_limit":"fup_limit", "source_package_name":"package_name", "source_sub_package":"sub_package", "package_price":"package_price", "custom_price":"custom_price", "special_discount":"special_discount", "additional_charges":"additional_charges", "balance_amount":"balance_amount", "current_balance":"wallet_balance_primary", "last_payment_source":"last_payment_source", "pop_technical_executive":"pop_technical_executive", "pop_collection_executive":"pop_collection_executive", "installation_address":"installation_address"}, data)
    subscriber.plan=plan; subscriber.nas=nas or subscriber.nas; subscriber.node=locations["node"] or subscriber.node; subscriber.pop=locations["pop"] or subscriber.pop; subscriber.switch=locations["switch"] or subscriber.switch
    subscriber.import_metadata={**subscriber.import_metadata, "last_import_id":str(row.import_batch_id), "wallet_balance_secondary":data.get("wallet_balance_secondary"), "raw_source":row.raw_data}
    subscriber.save()
    row.action = "UPDATE" if existing else "CREATE"; row.target_subscriber=subscriber
    return "updated" if existing else "created"


def commit_import(batch, options=None, retry_errors_only=False):
    if batch.status == "COMPLETED" and not retry_errors_only: return batch
    options = {**batch.options, **(options or {})}; batch.options=options; batch.status="PROCESSING"; batch.started_at=timezone.now(); batch.completed_at=None; batch.save()
    rows = batch.rows.filter(action="ERROR") if retry_errors_only else batch.rows.filter(action__in=["CREATE", "UPDATE", "SKIP"])
    counters = {"created":0,"updated":0,"skipped":0,"failed":0}
    ids = list(rows.values_list("id", flat=True))
    for start in range(0, len(ids), CHUNK_SIZE):
        with transaction.atomic():
            for row_id in ids[start:start+CHUNK_SIZE]:
                try:
                    with transaction.atomic():
                        row = SubscriberImportRow.objects.select_for_update().get(id=row_id)
                        if retry_errors_only:
                            normalized, errors = normalize_row(row.raw_data)
                            if errors: raise ValueError("; ".join(e["error"] for e in errors))
                            row.normalized_data=normalized; row.validation_errors=[]
                        result=process_row(row, options); counters[result]+=1; row.processing_error=""; row.processed_at=timezone.now(); row.save()
                except Exception as exc:
                    counters["failed"]+=1
                    SubscriberImportRow.objects.filter(id=row_id).update(action="ERROR", processing_error=str(exc)[:2000], processed_at=timezone.now())
    batch.created_rows += counters["created"]; batch.updated_rows += counters["updated"]; batch.skipped_rows += counters["skipped"]; batch.failed_rows = batch.rows.filter(action="ERROR").count(); batch.completed_at=timezone.now()
    batch.status = "PARTIAL" if batch.failed_rows else "COMPLETED"; batch.save()
    logger.info("Subscriber import processed", extra={"import_id":str(batch.id), **counters})
    return batch
