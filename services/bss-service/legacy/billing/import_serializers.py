from rest_framework import serializers

from customers.franchises import public_franchise_id

from .models import InvoiceImportBatch, InvoiceImportRow


class ImportUploadSerializer(serializers.Serializer):
    file=serializers.FileField(); franchise_id=serializers.IntegerField(required=False); tenant_id=serializers.IntegerField(required=False)
    update_existing=serializers.BooleanField(default=False); create_missing_packages=serializers.BooleanField(default=False); dry_run=serializers.BooleanField(default=True)
    def validate(self,attrs):
        attrs["franchise_id"]=attrs.get("franchise_id") or attrs.get("tenant_id")
        if not attrs.get("franchise_id"): raise serializers.ValidationError({"franchise_id":"This field is required."})
        upload=attrs["file"]
        if not upload.name.lower().endswith(".csv"): raise serializers.ValidationError({"file":"Only .csv files are accepted."})
        if upload.size>self.context["max_size"]: raise serializers.ValidationError({"file":f"File exceeds the {self.context['max_size']}-byte limit."})
        if getattr(upload,"content_type","") and upload.content_type not in ("text/csv","application/csv","application/vnd.ms-excel","text/plain","application/octet-stream"): raise serializers.ValidationError({"file":"Unsupported CSV MIME type."})
        return attrs


class ImportCommitSerializer(serializers.Serializer):
    update_existing=serializers.BooleanField(required=False); create_missing_packages=serializers.BooleanField(required=False)


class ImportRowSerializer(serializers.ModelSerializer):
    target_invoice_id=serializers.IntegerField(read_only=True)
    class Meta:
        model=InvoiceImportRow
        fields=("id","source_row_number","source_invoice_number","username","raw_data","normalized_data","action","validation_errors","processing_error","target_invoice_id","processed_at")


class ImportBatchSerializer(serializers.ModelSerializer):
    franchise_id=serializers.SerializerMethodField(); created_by_id=serializers.IntegerField(read_only=True); error_download_url=serializers.SerializerMethodField()
    class Meta: model=InvoiceImportBatch; exclude=("file",)
    def get_franchise_id(self,obj): return public_franchise_id(obj.franchise)
    def get_error_download_url(self,obj):
        path=f"/api/v1/invoice-imports/{obj.id}/errors/download/"; request=self.context.get("request"); return request.build_absolute_uri(path) if request else path
