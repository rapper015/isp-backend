from django.db import models

from subscribers.models import Subscriber


class ActiveSession(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        DISCONNECTING = "disconnecting", "Disconnecting"

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="active_sessions"
    )
    username = models.CharField(max_length=128, db_index=True)
    session_id = models.CharField(max_length=255, unique=True, db_index=True)
    nas_ip_address = models.CharField(max_length=64)
    nas_identifier = models.CharField(max_length=128, blank=True)
    framed_ip_address = models.CharField(max_length=64, blank=True)
    called_station_id = models.CharField(max_length=128, blank=True)
    calling_station_id = models.CharField(max_length=128, blank=True)
    started_at = models.DateTimeField(db_index=True)
    last_interim_at = models.DateTimeField(null=True, blank=True)
    upload_bytes = models.BigIntegerField(default=0)
    download_bytes = models.BigIntegerField(default=0)
    session_time_seconds = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ONLINE
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    nas = models.ForeignKey("network.NasDevice", null=True, blank=True, on_delete=models.PROTECT, related_name="active_sessions")

    def __str__(self) -> str:
        return f"{self.username} - {self.session_id}"


class AccountingRecord(models.Model):
    class EventType(models.TextChoices):
        START = "start", "Start"
        INTERIM = "interim", "Interim"
        STOP = "stop", "Stop"

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accounting_records",
    )
    username = models.CharField(max_length=128, db_index=True)
    session_id = models.CharField(max_length=255, db_index=True)
    event_type = models.CharField(
        max_length=16, choices=EventType.choices, db_index=True
    )
    nas_ip_address = models.CharField(max_length=64)
    framed_ip_address = models.CharField(max_length=64, blank=True)
    session_time_seconds = models.BigIntegerField(default=0)
    input_octets = models.BigIntegerField(default=0)
    output_octets = models.BigIntegerField(default=0)
    terminate_cause = models.CharField(max_length=128, blank=True)
    payload = models.JSONField()
    event_at = models.DateTimeField(db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    nas = models.ForeignKey("network.NasDevice", null=True, blank=True, on_delete=models.PROTECT, related_name="accounting_records")

    def __str__(self) -> str:
        return f"{self.username} - {self.session_id} - {self.event_type}"


class AuthLog(models.Model):
    class Action(models.TextChoices):
        AUTHENTICATE = "authenticate", "Authenticate"
        AUTHORIZE = "authorize", "Authorize"
        POST_AUTH = "post_auth", "Post Auth"
        DISCONNECT = "disconnect", "Disconnect"
        COA = "coa", "CoA"

    class Result(models.TextChoices):
        ACCEPT = "accept", "Accept"
        REJECT = "reject", "Reject"
        OK = "ok", "OK"

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auth_logs",
    )
    username = models.CharField(max_length=128, db_index=True)
    action = models.CharField(max_length=16, choices=Action.choices)
    result = models.CharField(max_length=16, choices=Result.choices, db_index=True)
    message = models.CharField(max_length=255)
    request_payload = models.JSONField()
    response_payload = models.JSONField()
    logged_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self) -> str:
        return f"{self.username} - {self.action} - {self.result}"
