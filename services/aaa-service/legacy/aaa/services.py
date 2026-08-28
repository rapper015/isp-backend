import logging
import math
import re
from typing import Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from network.models import NasDevice
from subscribers.models import Subscriber
from subscribers.services import verify_password

from .models import AccountingRecord, ActiveSession, AuthLog

logger = logging.getLogger("aaa")


# ---------------------------------------------------------------------------
# Flexible payload extraction (accepts either the platform's own camelCase
# keys or raw RADIUS attribute names, mirroring aaa.service.ts).
# ---------------------------------------------------------------------------


def get_string(payload: dict, keys: list[str], fallback: str = "") -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return fallback


def get_number(payload: dict, keys: list[str], fallback: float = 0.0) -> float:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return fallback
            return parsed if math.isfinite(parsed) and parsed >= 0 else fallback
    return fallback


def get_octets(payload: dict, octet_key: str, gigaword_key: str, app_key: str) -> float:
    app_value = get_number(payload, [app_key], float("nan"))
    if math.isfinite(app_value):
        return app_value

    octets = get_number(payload, [octet_key])
    gigawords = get_number(payload, [gigaword_key])
    return gigawords * 4294967296 + octets


def normalize_accounting_event(value: str) -> str:
    normalized = value.lower()
    if "start" in normalized:
        return "start"
    if "stop" in normalized:
        return "stop"
    return "interim"


def normalize_accounting_date(value: str):
    if not value:
        return timezone.now()

    parsed = parse_datetime(value) or parse_datetime(value.replace(" ", "T"))
    if parsed is None:
        return timezone.now()

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def normalize_accounting_payload(payload: dict) -> dict:
    username = get_string(payload, ["username", "User-Name"], "unknown")
    event_at = normalize_accounting_date(
        get_string(payload, ["eventAt", "Event-Timestamp", "Timestamp"])
    )
    session_id = get_string(payload, ["sessionId", "Acct-Session-Id"]) or (
        f"missing-session-{username}-{int(event_at.timestamp() * 1000)}"
    )

    return {
        "username": username,
        "session_id": session_id,
        "event_type": normalize_accounting_event(
            get_string(payload, ["eventType", "Acct-Status-Type"], "interim")
        ),
        "input_octets": get_octets(payload, "Acct-Input-Octets", "Acct-Input-Gigawords", "inputOctets"),
        "output_octets": get_octets(payload, "Acct-Output-Octets", "Acct-Output-Gigawords", "outputOctets"),
        "session_time_seconds": get_number(payload, ["sessionTimeSeconds", "Acct-Session-Time"]),
        "terminate_cause": get_string(payload, ["terminateCause", "Acct-Terminate-Cause"]),
        "event_at": event_at,
        "nas_ip_address": get_string(payload, ["nasIpAddress", "NAS-IP-Address"], "0.0.0.0"),
        "nas_identifier": get_string(payload, ["nasIdentifier", "NAS-Identifier"]),
        "framed_ip_address": get_string(payload, ["framedIpAddress", "Framed-IP-Address"]),
        "called_station_id": get_string(payload, ["calledStationId", "Called-Station-Id"]),
        "calling_station_id": get_string(payload, ["callingStationId", "Calling-Station-Id"]),
        "service_type": get_string(payload, ["serviceType", "Service-Type"]),
        "raw": payload,
    }


# ---------------------------------------------------------------------------
# Shared lookups / logging
# ---------------------------------------------------------------------------


MAC_ADDRESS_KEYS = ["callingStationId", "Calling-Station-Id", "macAddress"]

# "serviceType" is a literal string set per FreeRADIUS virtual server config
# (see docs/freeradius-isp-platform-rest.conf in isp-express-main), not a
# RADIUS Service-Type AVP. A dedicated MAC-auth virtual server sends
# "serviceType": "mac"; PPPoE/Hotspot servers send "pppoe" / "hotspot" and
# must match the subscriber's provisioned service_type.
SERVICE_TYPES_REQUIRING_MATCH = {"pppoe", "hotspot"}


def get_subscriber_context(username: str) -> tuple[Subscriber, "Plan", "Customer"]:
    from .exceptions import AppError

    subscriber = (
        Subscriber.objects.select_related("plan", "customer")
        .filter(username=username, deleted_at__isnull=True)
        .first()
    )
    if subscriber is None:
        raise AppError("Subscriber not found", 404)

    plan = subscriber.plan
    customer = subscriber.customer
    if plan is None or customer is None:
        raise AppError("Subscriber context is incomplete", 400)

    return subscriber, plan, customer


def _mac_lookup_candidates(raw_mac: str) -> list[str]:
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw_mac)
    if len(hex_only) != 12:
        return []

    candidates = set()
    for cased in (hex_only.lower(), hex_only.upper()):
        candidates.add(cased)
        candidates.add(":".join(cased[i : i + 2] for i in range(0, 12, 2)))
        candidates.add("-".join(cased[i : i + 2] for i in range(0, 12, 2)))
    return list(candidates)


def get_subscriber_by_mac(raw_mac: str) -> tuple[Subscriber, "Plan", "Customer"]:
    from .exceptions import AppError

    candidates = _mac_lookup_candidates(raw_mac)
    subscriber = (
        Subscriber.objects.select_related("plan", "customer")
        .filter(mac_address__in=candidates, deleted_at__isnull=True)
        .first()
        if candidates
        else None
    )
    if subscriber is None:
        raise AppError("Subscriber not found", 404)

    plan = subscriber.plan
    customer = subscriber.customer
    if plan is None or customer is None:
        raise AppError("Subscriber context is incomplete", 400)

    return subscriber, plan, customer


def _log(
    action: str,
    result: str,
    message: str,
    username: str,
    request_payload,
    response_payload,
    subscriber: Optional[Subscriber] = None,
) -> None:
    AuthLog.objects.create(
        subscriber=subscriber,
        username=username,
        action=action,
        result=result,
        message=message,
        request_payload=request_payload,
        response_payload=response_payload,
    )


# ---------------------------------------------------------------------------
# Public AAA operations
# ---------------------------------------------------------------------------


def authenticate(payload: dict) -> dict:
    username = payload.get("username", "")
    password = str(payload.get("password", ""))
    calling_station_id = get_string(payload, MAC_ADDRESS_KEYS)
    requested_service_type = get_string(payload, ["serviceType", "Service-Type"]).lower()

    auth_method = "mac" if requested_service_type == "mac" else "password"
    log_identity = username or calling_station_id

    try:
        if auth_method == "mac":
            subscriber, plan, customer = get_subscriber_by_mac(calling_station_id or username)
            is_expired = bool(subscriber.expires_at) and subscriber.expires_at < timezone.now()
            eligible = (
                customer.status == "active"
                and subscriber.status == "active"
                and plan.status == "active"
                and not is_expired
            )
            reject_message = "Subscriber is not eligible for service"
        else:
            subscriber, plan, customer = get_subscriber_context(username)
            password_matches = verify_password(password, subscriber.password_hash)
            is_expired = bool(subscriber.expires_at) and subscriber.expires_at < timezone.now()
            service_type_matches = (
                requested_service_type not in SERVICE_TYPES_REQUIRING_MATCH
                or requested_service_type == subscriber.service_type
            )

            eligible = (
                password_matches
                and customer.status == "active"
                and subscriber.status == "active"
                and plan.status == "active"
                and not is_expired
                and service_type_matches
            )
            reject_message = (
                "Subscriber is not provisioned for this service type"
                if password_matches and not service_type_matches
                else "Subscriber is not eligible for service"
            )

        if eligible:
            response = {
                "outcome": "Access-Accept",
                "replyMessage": "Subscriber authenticated successfully",
                "authMethod": auth_method,
                "attributes": {
                    "serviceType": subscriber.service_type,
                    "planCode": plan.plan_code,
                },
            }
        else:
            response = {
                "outcome": "Access-Reject",
                "replyMessage": reject_message,
                "authMethod": auth_method,
            }

        _log(
            "authenticate",
            "accept" if eligible else "reject",
            response["replyMessage"],
            log_identity,
            payload,
            response,
            subscriber,
        )
        return response
    except Exception:
        logger.exception("Internal AAA authentication failed for username=%s", log_identity)
        response = {
            "outcome": "Access-Reject",
            "replyMessage": "Authentication failed",
            "authMethod": auth_method,
        }
        _log("authenticate", "reject", "Authentication failed", log_identity, payload, response)
        return response


def authorize(payload: dict) -> dict:
    username = payload.get("username", "")
    calling_station_id = get_string(payload, MAC_ADDRESS_KEYS)
    requested_service_type = get_string(payload, ["serviceType", "Service-Type"]).lower()

    if requested_service_type == "mac":
        subscriber, plan, _customer = get_subscriber_by_mac(calling_station_id or username)
    else:
        subscriber, plan, _customer = get_subscriber_context(username)

    response = {
        "outcome": "Access-Accept",
        "replyMessage": "Authorization profile returned",
        "attributes": {
            "Mikrotik-Rate-Limit": f"{plan.upload_rate_kbps}k/{plan.download_rate_kbps}k",
            "Filter-Id": plan.speed_profile_name,
            "Framed-Pool": plan.ip_pool or "",
            "Tunnel-Private-Group-ID": plan.vlan or "",
            "staticIpAddress": subscriber.static_ip_address or None,
        },
    }

    _log("authorize", "accept", response["replyMessage"], username, payload, response, subscriber)
    return response


def accounting(payload: dict) -> dict:
    normalized = normalize_accounting_payload(payload)
    subscriber = Subscriber.objects.filter(username=normalized["username"]).first()

    AccountingRecord.objects.create(
        subscriber=subscriber,
        username=subscriber.username if subscriber else normalized["username"],
        session_id=normalized["session_id"],
        event_type=normalized["event_type"],
        nas_ip_address=normalized["nas_ip_address"],
        framed_ip_address=normalized["framed_ip_address"],
        session_time_seconds=int(round(normalized["session_time_seconds"])),
        input_octets=int(round(normalized["input_octets"])),
        output_octets=int(round(normalized["output_octets"])),
        terminate_cause=normalized["terminate_cause"],
        payload=normalized["raw"],
        event_at=normalized["event_at"],
        nas=NasDevice.objects.filter(radius_source_ip=normalized["nas_ip_address"],deleted_at__isnull=True).first(),
    )

    NasDevice.objects.update_or_create(
        franchise=None,
        nas_ip_address=normalized["nas_ip_address"],
        defaults={
            "nas_identifier": normalized["nas_identifier"],
            "name": normalized["nas_identifier"] or normalized["nas_ip_address"],
            "status": "offline" if normalized["event_type"] == "stop" else "online",
            "last_seen_at": normalized["event_at"],
            "service_types": [normalized["service_type"]] if normalized["service_type"] else [],
        },
    )

    if subscriber is None:
        return {"outcome": "OK", "replyMessage": "Accounting event stored without subscriber match"}

    if normalized["event_type"] == "start":
        ActiveSession.objects.update_or_create(
            session_id=normalized["session_id"],
            defaults={
                "subscriber": subscriber,
                "username": subscriber.username,
                "nas_ip_address": normalized["nas_ip_address"],
                "nas_identifier": normalized["nas_identifier"],
                "framed_ip_address": normalized["framed_ip_address"],
                "called_station_id": normalized["called_station_id"],
                "calling_station_id": normalized["calling_station_id"],
                "started_at": normalized["event_at"],
                "last_interim_at": normalized["event_at"],
                "upload_bytes": int(round(normalized["input_octets"])),
                "download_bytes": int(round(normalized["output_octets"])),
                "session_time_seconds": int(round(normalized["session_time_seconds"])),
                "status": "online",
                "nas": NasDevice.objects.filter(radius_source_ip=normalized["nas_ip_address"],deleted_at__isnull=True).first(),
            },
        )
        subscriber.last_online_at = normalized["event_at"]
        subscriber.save(update_fields=["last_online_at"])

    if normalized["event_type"] == "interim":
        ActiveSession.objects.filter(session_id=normalized["session_id"]).update(
            last_interim_at=normalized["event_at"],
            upload_bytes=int(round(normalized["input_octets"])),
            download_bytes=int(round(normalized["output_octets"])),
            session_time_seconds=int(round(normalized["session_time_seconds"])),
            framed_ip_address=normalized["framed_ip_address"],
        )

    if normalized["event_type"] == "stop":
        ActiveSession.objects.filter(session_id=normalized["session_id"]).delete()

    return {"outcome": "OK", "replyMessage": "Accounting event processed"}


def post_auth(payload: dict) -> dict:
    username = payload.get("username", "")
    response = {"outcome": "OK", "replyMessage": "Post-auth event acknowledged"}
    _log("post_auth", "ok", response["replyMessage"], username, payload, response)
    return response


def disconnect(payload: dict) -> dict:
    username = payload.get("username", "")
    session_id = payload.get("sessionId")
    if session_id:
        ActiveSession.objects.filter(session_id=session_id).update(status="disconnecting")

    response = {"outcome": "OK", "replyMessage": "Disconnect instruction accepted"}
    _log("disconnect", "ok", response["replyMessage"], username, payload, response)
    return response


def coa(payload: dict) -> dict:
    username = payload.get("username", "")
    response = {
        "outcome": "OK",
        "replyMessage": "CoA request accepted for future network enforcement",
    }
    _log("coa", "ok", response["replyMessage"], username, payload, response)
    return response
