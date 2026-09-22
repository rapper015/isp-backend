#!/usr/bin/env python3
"""Translate FreeRADIUS exec calls to the private AAA JSON contract."""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

with open("/run/freeradius-aaa.json", encoding="utf-8") as config_file:
    CONFIG = json.load(config_file)
BASE_URL = CONFIG["base_url"].rstrip("/")
SERVICE_KEY = CONFIG["service_key"]


def value(index: int) -> str:
    return sys.argv[index] if len(sys.argv) > index else ""


def integer(raw: str) -> int:
    try:
        return max(0, int(raw or "0"))
    except ValueError:
        return 0


def request(endpoint: str, attributes: dict, idempotency_key=None) -> dict:
    payload = {"correlation_id": str(uuid.uuid4()), "attributes": attributes}
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-AAA-Service-Key": SERVICE_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.loads(response.read())


def emit_reply(attributes: dict) -> None:
    for name, raw in attributes.items():
        if isinstance(raw, bool):
            rendered = "yes" if raw else "no"
        elif isinstance(raw, int):
            rendered = str(raw)
        else:
            rendered = '"' + str(raw).replace("\\", "\\\\").replace('"', '\\"') + '"'
        print(f"{name} := {rendered}")


def authenticate() -> int:
    attributes = {
        "User-Name": value(2),
        "User-Password": value(3),
        "NAS-IP-Address": value(4),
        "NAS-Identifier": value(5),
        "Calling-Station-Id": value(6),
        "Service-Type": "pppoe",
    }
    result = request("/internal/radius/v1/authenticate", {k: v for k, v in attributes.items() if v})
    if result.get("outcome") != "Access-Accept":
        return 1
    emit_reply(result.get("reply_attributes") or {})
    return 0


def accounting() -> int:
    attributes = {
        "User-Name": value(2),
        "NAS-IP-Address": value(3),
        "NAS-Identifier": value(4),
        "Acct-Session-Id": value(5),
        "Acct-Status-Type": value(6),
        "Acct-Input-Octets": integer(value(7)),
        "Acct-Input-Gigawords": integer(value(8)),
        "Acct-Output-Octets": integer(value(9)),
        "Acct-Output-Gigawords": integer(value(10)),
        "Framed-IP-Address": value(11),
    }
    cleaned = {k: v for k, v in attributes.items() if v != ""}
    key = f"{cleaned.get('NAS-IP-Address')}:{cleaned.get('Acct-Session-Id')}:{cleaned.get('Acct-Status-Type')}:{cleaned.get('Acct-Input-Octets')}:{cleaned.get('Acct-Output-Octets')}"
    result = request("/internal/radius/v1/accounting", cleaned, key)
    return 0 if result.get("outcome") == "OK" else 1


try:
    action = value(1)
    code = authenticate() if action == "auth" else accounting() if action == "accounting" else 1
except (OSError, ValueError, KeyError, urllib.error.URLError, json.JSONDecodeError):
    code = 1
raise SystemExit(code)
