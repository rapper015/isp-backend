from django.http import FileResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.casing import normalize_keys

from .models import KycDocument
from .serializers import KycDocumentDetailSerializer, KycDocumentSerializer


def _normalized_payload(request) -> dict:
    data = normalize_keys(request.data)
    if "customer_id" in data and "customer" not in data:
        data["customer"] = data.pop("customer_id")
    return data


def _admin_id(request):
    admin = request.user or {}
    admin_id = admin.get("userId")
    return int(admin_id) if admin_id else None


class KycDocumentListCreateView(AdminAPIView):
    def get(self, request):
        documents = KycDocument.objects.filter(deleted_at__isnull=True).order_by(
            "-created_at"
        )

        customer_id = request.query_params.get("customerId") or request.query_params.get(
            "customer_id"
        )
        if customer_id:
            documents = documents.filter(customer_id=customer_id)

        status_param = request.query_params.get("status")
        if status_param:
            documents = documents.filter(status=status_param)

        document_type = request.query_params.get("documentType") or request.query_params.get(
            "document_type"
        )
        if document_type:
            documents = documents.filter(document_type=document_type)

        return Response(KycDocumentDetailSerializer(documents, many=True).data)

    def post(self, request):
        serializer = KycDocumentSerializer(data=_normalized_payload(request))
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by_id=_admin_id(request))
        return Response(
            KycDocumentDetailSerializer(serializer.instance).data, status=HTTP_201_CREATED
        )


class KycDocumentDetailView(AdminAPIView):
    def get(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None:
            raise AppError("KYC document not found", 404)
        return Response(KycDocumentDetailSerializer(document).data)

    def patch(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None:
            raise AppError("KYC document not found", 404)
        serializer = KycDocumentSerializer(
            document, data=_normalized_payload(request), partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(KycDocumentDetailSerializer(serializer.instance).data)

    def delete(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None:
            raise AppError("KYC document not found", 404)
        document.deleted_at = timezone.now()
        document.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)


class KycDocumentVerifyView(AdminAPIView):
    def post(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None:
            raise AppError("KYC document not found", 404)

        document.status = KycDocument.Status.VERIFIED
        document.rejection_reason = ""
        document.verified_by_id = _admin_id(request)
        document.verified_at = timezone.now()
        document.save(
            update_fields=["status", "rejection_reason", "verified_by", "verified_at"]
        )
        return Response(KycDocumentDetailSerializer(document).data)


class KycDocumentRejectView(AdminAPIView):
    def post(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None:
            raise AppError("KYC document not found", 404)

        reason = normalize_keys(request.data).get("reason", "").strip()
        if not reason:
            raise AppError(
                "Validation failed", 400, {"missingFields": ["reason"]}
            )

        document.status = KycDocument.Status.REJECTED
        document.rejection_reason = reason
        document.verified_by_id = _admin_id(request)
        document.verified_at = timezone.now()
        document.save(
            update_fields=["status", "rejection_reason", "verified_by", "verified_at"]
        )
        return Response(KycDocumentDetailSerializer(document).data)


class KycDocumentFileView(AdminAPIView):
    """Streams the stored file - kept out of MEDIA_URL so it stays behind admin auth."""

    def get(self, request, document_id):
        document = KycDocument.objects.filter(
            id=document_id, deleted_at__isnull=True
        ).first()
        if document is None or not document.file:
            raise AppError("KYC document not found", 404)

        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.file.name.rsplit("/", 1)[-1],
        )
