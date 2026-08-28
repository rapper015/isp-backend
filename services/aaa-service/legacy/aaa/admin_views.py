from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError

from .models import AccountingRecord, ActiveSession
from .serializers import AccountingRecordSerializer, ActiveSessionSerializer


class ActiveSessionListView(AdminAPIView):
    def get(self, request):
        sessions = ActiveSession.objects.filter(deleted_at__isnull=True).order_by("-started_at")
        return Response(ActiveSessionSerializer(sessions, many=True).data)


class SessionDisconnectView(AdminAPIView):
    def post(self, request, session_id):
        session = ActiveSession.objects.filter(
            session_id=session_id, deleted_at__isnull=True
        ).first()
        if session is None:
            return Response({"message": "Session not found", "session": None})

        session.status = ActiveSession.Status.DISCONNECTING
        session.save(update_fields=["status", "updated_at"])
        return Response(
            {"message": "Disconnect requested", "session": ActiveSessionSerializer(session).data}
        )


class SessionDetailView(AdminAPIView):
    def delete(self, request, session_id):
        session = ActiveSession.objects.filter(
            session_id=session_id, deleted_at__isnull=True
        ).first()
        if session is None:
            raise AppError("Session not found", 404)
        session.deleted_at = timezone.now()
        session.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)


class AccountingRecordListView(AdminAPIView):
    def get(self, request):
        records = AccountingRecord.objects.filter(deleted_at__isnull=True).order_by(
            "-event_at"
        )[:500]
        return Response(AccountingRecordSerializer(records, many=True).data)


class AccountingRecordDetailView(AdminAPIView):
    def delete(self, request, record_id):
        record = AccountingRecord.objects.filter(
            id=record_id, deleted_at__isnull=True
        ).first()
        if record is None:
            raise AppError("Accounting record not found", 404)
        record.deleted_at = timezone.now()
        record.save(update_fields=["deleted_at"])
        return Response(status=HTTP_204_NO_CONTENT)
