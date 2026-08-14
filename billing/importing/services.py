import hashlib
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.models import Invoice, InvoiceImportBatch, InvoiceImportRow, LedgerEntry
from customers.models import Franchise
from payments.models import Payment
from plans.models import Plan
from subscribers.models import Subscriber

from .parser import SOURCE_SYSTEM, normalize_row, read_csv

logger=logging.getLogger(__name__); CHUNK_SIZE=250


def file_digest(upload):
    digest=hashlib.sha256(); upload.seek(0)
    for chunk in upload.chunks(): digest.update(chunk)
    upload.seek(0); return digest.hexdigest()


def find_subscriber(data, franchise):
    matches=set()
    if data.get("account_number"):
        matches.update(Subscriber.objects.filter(franchise=franchise,external_id=data["account_number"],deleted_at__isnull=True).values_list("id",flat=True))
    if data.get("username"):
        matches.update(Subscriber.objects.filter(franchise=franchise,username__iexact=data["username"],deleted_at__isnull=True).values_list("id",flat=True))
    if data.get("ip_address"):
        matches.update(Subscriber.objects.filter(franchise=franchise,static_ip_address=data["ip_address"],deleted_at__isnull=True).values_list("id",flat=True))
    if len(matches)>1: return None,"Account number, username, or IP match different subscribers"
    if not matches: return None,"No subscriber matched account number, username, or IP in the selected franchise"
    return Subscriber.objects.select_related("customer","plan").get(id=next(iter(matches))),None


def find_plan(data, franchise, subscriber=None):
    plan=Plan.objects.filter(Q(franchise=franchise)|Q(franchise__isnull=True),name__iexact=data["package_name"],deleted_at__isnull=True).first()
    if not plan and subscriber and subscriber.source_package_name and subscriber.source_package_name.casefold()==data["package_name"].casefold(): plan=subscriber.plan
    return plan


def validate_upload(upload,franchise,created_by,options):
    batch=InvoiceImportBatch.objects.create(franchise=franchise,original_filename=upload.name,file_hash=file_digest(upload),file=upload,status="VALIDATING",options=options,created_by=created_by,started_at=timezone.now())
    try:
        with batch.file.open("rb") as stored: headers,keys,parsed_rows=read_csv(stored)
        rows=[]; counts={"create_count":0,"update_count":0,"skip_count":0,"duplicate_count":0}; seen=set()
        for row_number,raw,structural_errors in parsed_rows:
            data,errors=normalize_row(raw) if raw else ({},list(structural_errors))
            if raw: errors.extend(structural_errors)
            if data.get("franchise_name") and data["franchise_name"].casefold()!=franchise.name.casefold(): errors.append({"field":"franchise_name","value":data["franchise_name"],"error":"Row franchise does not match the selected franchise"})
            source_number=data.get("invoice_number") or ""
            if source_number in seen: errors.append({"field":"invoice_number","value":source_number,"error":"Duplicate invoice number within file"}); counts["duplicate_count"]+=1
            seen.add(source_number)
            subscriber,identity_error=find_subscriber(data,franchise) if data.get("username") else (None,None)
            if identity_error: errors.append({"field":"identity","value":data.get("username") or "","error":identity_error})
            plan=find_plan(data,franchise,subscriber)
            if subscriber and not plan and not options.get("create_missing_packages"): errors.append({"field":"package_name","value":data.get("package_name") or "","error":"Package does not exist"})
            existing=Invoice.objects.filter(subscriber=subscriber,source_system=SOURCE_SYSTEM,source_invoice_number=source_number).first() if subscriber else None
            action="ERROR" if errors else ("UPDATE" if existing and options.get("update_existing") else "SKIP" if existing else "CREATE")
            counts[{"CREATE":"create_count","UPDATE":"update_count","SKIP":"skip_count"}.get(action,"skip_count")]+=0 if action=="ERROR" else 1
            rows.append(InvoiceImportRow(import_batch=batch,source_row_number=row_number,source_invoice_number=source_number,username=data.get("username") or "",raw_data=raw,normalized_data=data,action=action,validation_errors=errors,target_invoice=existing))
        InvoiceImportRow.objects.bulk_create(rows,batch_size=CHUNK_SIZE)
        batch.total_rows=len(rows); batch.invalid_rows=sum(r.action=="ERROR" for r in rows); batch.valid_rows=len(rows)-batch.invalid_rows; batch.duplicate_rows=counts["duplicate_count"]
        duplicate_file=InvoiceImportBatch.objects.filter(franchise=franchise,file_hash=batch.file_hash,status__in=["COMPLETED","PARTIAL"]).exclude(id=batch.id).exists()
        batch.validation_summary={**counts,"warnings":["This file hash was imported previously"] if duplicate_file else []}; batch.column_mapping={str(i):{"source":h,"target":keys[i]} for i,h in enumerate(headers)}; batch.status="VALIDATED"; batch.completed_at=timezone.now(); batch.save(); return batch
    except Exception:
        batch.status="FAILED"; batch.completed_at=timezone.now(); batch.save(update_fields=["status","completed_at"]); raise


def _create_plan(data,franchise):
    code=hashlib.sha1(f"invoice:{franchise.id}:{data['package_name']}".encode()).hexdigest()[:16].upper()
    return Plan.objects.create(franchise=franchise,plan_code=f"IMP-I-{code}",name=data["package_name"],source_name=data["package_name"],source_sub_package=data.get("sub_package") or "",description="Created by invoice CSV import",monthly_fee=Decimal(data.get("package_price") or "0"),speed_profile_name="imported-unmapped",download_rate_kbps=1,upload_rate_kbps=1,status=Plan.Status.INACTIVE)


def _payment_method(value):
    value=(value or "").casefold()
    if "cash" in value: return "cash"
    if any(word in value for word in ("bank","api","gateway","upi","online")): return "bank_transfer"
    if "card" in value: return "card"
    return "mobile_money"


def process_row(row,options):
    data=row.normalized_data; franchise=row.import_batch.franchise
    subscriber,error=find_subscriber(data,franchise)
    if error: raise ValueError(error)
    existing=Invoice.objects.filter(subscriber=subscriber,source_system=SOURCE_SYSTEM,source_invoice_number=data["invoice_number"]).first()
    if existing and not options.get("update_existing"): row.action="SKIP"; row.target_invoice=existing; return "skipped"
    plan=find_plan(data,franchise,subscriber)
    if not plan and options.get("create_missing_packages"): plan=_create_plan(data,franchise)
    if not plan: raise ValueError("Package does not exist")
    amount=Decimal(data["amount"]); paid=Decimal(data["paid_amount"]); status=data["status"]
    balance=Decimal("0") if status in ("paid","cancelled","void") else amount-paid
    tax=Decimal(data.get("total_tax") or data.get("tax") or "0")
    subtotal=amount-tax
    fields={"customer":subscriber.customer,"subscriber":subscriber,"plan":plan,"billing_period_start":parse_datetime(data["billing_period_start"]),"billing_period_end":parse_datetime(data["billing_period_end"]),"due_date":parse_datetime(data["due_date"]),"subtotal":subtotal,"tax_amount":tax,"amount":amount,"balance_due":balance,"status":status,"notes":data.get("comment") or "","source_system":SOURCE_SYSTEM,"source_invoice_number":data["invoice_number"],"source_order_number":data.get("order_number") or "","line_items":[{"description":data["package_name"],"sub_package":data.get("sub_package"),"quantity":1,"unit_price":data.get("package_price") or str(subtotal),"total":str(subtotal)}],"import_metadata":{"last_import_id":str(row.import_batch_id),"source_invoice_date":data.get("invoice_date"),"invoice_type":data.get("invoice_type"),"renew_type":data.get("renew_type"),"reference_number":data.get("reference_number"),"payment_type":data.get("payment_type"),"raw_source":row.raw_data}}
    invoice=existing or Invoice(invoice_number=f"LEGACY-{franchise.id}-{data['invoice_number']}"[:64])
    for key,value in fields.items(): setattr(invoice,key,value)
    invoice.save()
    posted=parse_datetime(data["invoice_date"])
    LedgerEntry.objects.update_or_create(invoice=invoice,entry_type="invoice",description=f"Imported invoice {data['invoice_number']}",defaults={"customer":subscriber.customer,"subscriber":subscriber,"debit":amount,"credit":Decimal("0"),"balance_impact":amount,"posted_at":posted})
    payment_ref=f"IMP-P-{franchise.id}-{data['invoice_number']}"[:64]
    if paid>0:
        payment,_=Payment.objects.update_or_create(payment_reference=payment_ref,defaults={"invoice":invoice,"customer":subscriber.customer,"subscriber":subscriber,"amount":paid,"method":_payment_method(data.get("payment_type")),"received_at":parse_datetime(data.get("last_paid_at") or data["invoice_date"]),"notes":"Imported from legacy invoice CSV"})
        LedgerEntry.objects.update_or_create(payment=payment,entry_type="payment",defaults={"customer":subscriber.customer,"subscriber":subscriber,"invoice":invoice,"debit":Decimal("0"),"credit":paid,"balance_impact":-paid,"description":f"Imported payment {payment_ref}","posted_at":payment.received_at})
    row.action="UPDATE" if existing else "CREATE"; row.target_invoice=invoice
    return "updated" if existing else "created"


def commit_import(batch,options=None,retry_errors_only=False):
    if batch.status=="COMPLETED" and not retry_errors_only:return batch
    options={**batch.options,**(options or {})}; batch.options=options; batch.status="PROCESSING"; batch.started_at=timezone.now(); batch.completed_at=None; batch.save()
    qs=batch.rows.filter(action="ERROR") if retry_errors_only else batch.rows.filter(action__in=["CREATE","UPDATE","SKIP"]); ids=list(qs.values_list("id",flat=True)); counters={"created":0,"updated":0,"skipped":0,"failed":0}
    for start in range(0,len(ids),CHUNK_SIZE):
        for row_id in ids[start:start+CHUNK_SIZE]:
            try:
                with transaction.atomic():
                    row=InvoiceImportRow.objects.select_for_update().get(id=row_id)
                    if retry_errors_only:
                        normalized,errors=normalize_row(row.raw_data)
                        if errors: raise ValueError("; ".join(e["error"] for e in errors))
                        row.normalized_data=normalized; row.validation_errors=[]
                    result=process_row(row,options); counters[result]+=1; row.processing_error=""; row.processed_at=timezone.now(); row.save()
            except Exception as exc:
                counters["failed"]+=1; InvoiceImportRow.objects.filter(id=row_id).update(action="ERROR",processing_error=str(exc)[:2000],processed_at=timezone.now())
    batch.created_rows+=counters["created"]; batch.updated_rows+=counters["updated"]; batch.skipped_rows+=counters["skipped"]; batch.failed_rows=batch.rows.filter(action="ERROR").count(); batch.completed_at=timezone.now(); batch.status="PARTIAL" if batch.failed_rows else "COMPLETED"; batch.save(); logger.info("Invoice import processed",extra={"import_id":str(batch.id),**counters}); return batch
