from rest_framework import serializers

from .models import AccountingRecord, ActiveSession


class ActiveSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActiveSession
        fields = (
            "id",
            "subscriber",
            "username",
            "session_id",
            "nas_ip_address",
            "nas_identifier",
            "framed_ip_address",
            "called_station_id",
            "calling_station_id",
            "started_at",
            "last_interim_at",
            "upload_bytes",
            "download_bytes",
            "session_time_seconds",
            "status",
            "created_at",
            "updated_at",
        )


class AccountingRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountingRecord
        fields = (
            "id",
            "subscriber",
            "username",
            "session_id",
            "event_type",
            "nas_ip_address",
            "framed_ip_address",
            "session_time_seconds",
            "input_octets",
            "output_octets",
            "terminate_cause",
            "payload",
            "event_at",
            "created_at",
            "updated_at",
        )
