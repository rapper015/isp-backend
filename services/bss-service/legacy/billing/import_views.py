import csv
import json

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils.dateparse import parse_date
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED

from aaa.exceptions import AppError
from accounts.models import AdminUser
from accounts.views import AdminAPIView
from customers.franchises import resolve_franchise
from subscribers.import_permissions import CanImportSubscribers

from .import_serializers import ImportBatchSerializer,ImportCommitSerializer,ImportRowSerializer,ImportUploadSerializer
from .importing.parser import CSVStructureError
from .importing.services import commit_import,validate_upload
from .models import InvoiceImportBatch


class ImportPagination(PageNumberPagination): page_size=50; page_size_query_param="page_size"; max_page_size=250
class ImportAPIView(AdminAPIView):
    permission_classes=[CanImportSubscribers]
    def batch(self,import_id):
        batch=InvoiceImportBatch.objects.filter(id=import_id).first()
        if not batch: raise AppError("Invoice import not found",404)
        return batch
def _admin(request):
    admin=AdminUser.objects.filter(id=request.user.get("userId"),is_active=True).first()
    if not admin: raise AppError("Admin user not found",401)
    return admin
def _summary(batch):
    value=batch.validation_summary
    return {"total_rows":batch.total_rows,"valid_rows":batch.valid_rows,"invalid_rows":batch.invalid_rows,"create_count":value.get("create_count",0),"update_count":value.get("update_count",0),"skip_count":value.get("skip_count",0)}


class ImportValidateView(ImportAPIView):
    def post(self,request):
        serializer=ImportUploadSerializer(data=request.data,context={"max_size":settings.INVOICE_IMPORT_MAX_BYTES}); serializer.is_valid(raise_exception=True); data=serializer.validated_data
        franchise=resolve_franchise(data["franchise_id"])
        if not franchise: raise AppError("Franchise not found",400)
        options={k:data[k] for k in ("update_existing","create_missing_packages","dry_run")}
        try: batch=validate_upload(data["file"],franchise,_admin(request),options)
        except CSVStructureError as exc: raise AppError("CSV validation failed",400,{"file":[str(exc)]}) from exc
        samples=ImportRowSerializer(batch.rows.all()[:10],many=True).data
        return Response({"success":True,"import_id":str(batch.id),"status":batch.status,"summary":_summary(batch),"warnings":batch.validation_summary.get("warnings",[]),"sample_rows":samples,"error_download_url":None if not batch.invalid_rows else f"/api/v1/invoice-imports/{batch.id}/errors/download/"},status=HTTP_201_CREATED)


class ImportListView(ImportAPIView):
    def get(self,request):
        qs=InvoiceImportBatch.objects.select_related("franchise","created_by").order_by("-created_at"); requested=request.query_params.get("franchise_id") or request.query_params.get("tenant_id")
        if requested:
            franchise=resolve_franchise(requested); qs=qs.filter(franchise=franchise) if franchise else qs.none()
        if request.query_params.get("status"): qs=qs.filter(status=request.query_params["status"].upper())
        if parse_date(request.query_params.get("date_from","")): qs=qs.filter(created_at__date__gte=parse_date(request.query_params["date_from"]))
        if parse_date(request.query_params.get("date_to","")): qs=qs.filter(created_at__date__lte=parse_date(request.query_params["date_to"]))
        paginator=ImportPagination(); page=paginator.paginate_queryset(qs,request); return paginator.get_paginated_response(ImportBatchSerializer(page,many=True,context={"request":request}).data)
class ImportDetailView(ImportAPIView):
    def get(self,request,import_id): return Response(ImportBatchSerializer(self.batch(import_id),context={"request":request}).data)
class ImportCommitView(ImportAPIView):
    def post(self,request,import_id):
        batch=self.batch(import_id)
        if batch.status not in ("VALIDATED","PARTIAL","COMPLETED"): raise AppError("Import is not ready to commit",409)
        serializer=ImportCommitSerializer(data=request.data); serializer.is_valid(raise_exception=True); return Response(ImportBatchSerializer(commit_import(batch,serializer.validated_data),context={"request":request}).data)
class ImportRowsView(ImportAPIView):
    def get(self,request,import_id):
        qs=self.batch(import_id).rows.all()
        if request.query_params.get("action"): qs=qs.filter(action=request.query_params["action"].upper())
        paginator=ImportPagination(); page=paginator.paginate_queryset(qs,request); return paginator.get_paginated_response(ImportRowSerializer(page,many=True).data)
def _safe(value):
    value="" if value is None else str(value); return "'"+value if value.startswith(("=","+","-","@")) else value
class Echo:
    def write(self,value): return value
class ImportErrorsDownloadView(ImportAPIView):
    def get(self,request,import_id):
        rows=self.batch(import_id).rows.filter(action="ERROR").iterator(chunk_size=250); writer=csv.writer(Echo())
        def stream():
            yield writer.writerow(["row_number","invoice_number","username","field","value","error","original_row_values"])
            for row in rows:
                errors=row.validation_errors or [{"field":"processing","value":"","error":row.processing_error}]; original=_safe(json.dumps(row.raw_data,ensure_ascii=False))
                for error in errors: yield writer.writerow([row.source_row_number,_safe(row.source_invoice_number),_safe(row.username),_safe(error.get("field")),_safe(error.get("value")),_safe(error.get("error")),original])
        response=StreamingHttpResponse(stream(),content_type="text/csv; charset=utf-8"); response["Content-Disposition"]=f'attachment; filename="invoice-import-{import_id}-errors.csv"'; return response
class ImportRetryView(ImportAPIView):
    def post(self,request,import_id):
        batch=self.batch(import_id)
        if batch.status not in ("PARTIAL","FAILED","VALIDATED"): raise AppError("Import has no retryable rows",409)
        serializer=ImportCommitSerializer(data=request.data); serializer.is_valid(raise_exception=True); return Response(ImportBatchSerializer(commit_import(batch,serializer.validated_data,retry_errors_only=True),context={"request":request}).data)
class ImportCancelView(ImportAPIView):
    def post(self,request,import_id):
        batch=self.batch(import_id)
        if batch.status not in ("UPLOADED","VALIDATING","PROCESSING"): raise AppError("Import cannot be cancelled in its current state",409)
        batch.status="CANCELLED"; batch.save(update_fields=["status"]); return Response(ImportBatchSerializer(batch,context={"request":request}).data)
