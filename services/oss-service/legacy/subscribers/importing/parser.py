import csv
import io
import ipaddress
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone


SOURCE_SYSTEM = "legacy_subscriber_csv"
REQUIRED_HEADERS = {"Id", "Username", "Name", "Package Name", "Franchise Name"}
DATE_FORMATS = ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d")
STATUS_MAP = {"active": "active", "inactive": "inactive", "expired": "inactive"}
BOOLEAN_MAP = {"yes": True, "true": True, "1": True, "no": False, "false": False, "0": False}
HEADER_KEYS = {
    "Id": "external_id", "CAF No": "caf_number", "Status": "status", "Outage": "outage_enabled",
    "Account Type": "account_type", "Franchise Name": "franchise_name", "Username": "username",
    "MAC": "mac_address", "Name": "full_name", "Father/Company Name": "father_or_company_name",
    "Package Name": "package_name", "Sub Package": "sub_package", "Mobile": "primary_mobile",
    "Alt. Mobile": "alternate_mobile", "IpAddress": "ip_address", "Expiry Date": "expiry_at",
    "Last Renewal": "last_renewal_at", "FUP Limit": "fup_limit", "Branch": "branch", "Area": "area",
    "Colony": "colony", "Building": "building", "City": "city", "State": "state", "Node": "node",
    "Pop": "pop", "Switch": "switch", "Door No": "door_number", "Billing Address": "billing_address",
    "Installation Address": "installation_address", "GSTIN": "gstin", "Email": "email",
    "Date Added": "source_added_at", "Allowed MACs": "allowed_macs", "NAS IP": "nas_ip",
    "POP Tech Exe": "pop_technical_executive", "POP Coll Exe": "pop_collection_executive",
    "Last Payment Source": "last_payment_source", "Package Price": "package_price", "Custom Price": "custom_price",
    "Balance Amount": "balance_amount", "Last Logoff": "last_logoff_at", "Nas Port Id": "nas_port_id",
    "Spl. Discount": "special_discount", "Add. Charges": "additional_charges", "Latitude": "latitude",
    "Longitude": "longitude", "Auto Renew": "auto_renew", "Connection Type": "connection_type",
    "CAF Form": "caf_form_available", "Address Proof": "address_proof_available",
    "Identity Proof": "identity_proof_available", "Customer Pic": "customer_picture_available",
    "User Added": "source_created_by", "Commitment Date": "commitment_date",
}


class CSVStructureError(ValueError):
    pass


def read_csv(upload):
    upload.seek(0)
    raw = upload.read()
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CSVStructureError("File must be UTF-8 or UTF-8 with BOM") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise CSVStructureError("CSV header is missing or malformed") from exc
    headers = [h.strip() for h in headers]
    missing = sorted(REQUIRED_HEADERS - set(headers))
    if missing:
        raise CSVStructureError(f"Missing required columns: {', '.join(missing)}")
    duplicates = {h for h in headers if headers.count(h) > 1}
    if duplicates - {"Wallet Balance"}:
        raise CSVStructureError(f"Unsupported duplicate headers: {', '.join(sorted(duplicates - {'Wallet Balance'}))}")
    keys, wallet_index = [], 0
    for header in headers:
        if header == "Wallet Balance":
            wallet_index += 1
            keys.append(f"wallet_balance_{'primary' if wallet_index == 1 else 'secondary'}")
        else:
            keys.append(HEADER_KEYS.get(header, f"unmapped__{header}"))
    rows = []
    try:
        for number, values in enumerate(reader, start=2):
            if len(values) != len(headers):
                rows.append((number, {}, [{"field": "row", "value": "", "error": f"Expected {len(headers)} columns, got {len(values)}"}]))
                continue
            rows.append((number, {keys[i]: values[i] for i in range(len(keys))}, []))
    except csv.Error as exc:
        raise CSVStructureError(f"Malformed CSV near row {reader.line_num}: {exc}") from exc
    return headers, keys, rows


def _blank(value):
    value = value.strip() if isinstance(value, str) else value
    return None if value == "" else value


def _error(errors, field, value, message):
    errors.append({"field": field, "value": value or "", "error": message})


def normalize_row(raw):
    data = {key: _blank(value) for key, value in raw.items()}
    errors = []
    for field in ("external_id", "username", "full_name", "package_name", "franchise_name"):
        if not data.get(field):
            _error(errors, field, data.get(field), "This field is required")
    status_raw = data.get("status")
    if status_raw:
        normalized = STATUS_MAP.get(status_raw.casefold())
        if normalized is None:
            _error(errors, "status", status_raw, "Unknown status")
        else:
            data["status"] = normalized
    for field in ("outage_enabled", "auto_renew", "caf_form_available", "address_proof_available", "identity_proof_available", "customer_picture_available"):
        value = data.get(field)
        if value is not None:
            parsed = BOOLEAN_MAP.get(value.casefold())
            if parsed is None:
                _error(errors, field, value, "Expected Yes/No, true/false, or 1/0")
            else:
                data[field] = parsed
    for field in ("primary_mobile", "alternate_mobile"):
        value = data.get(field)
        if value and not re.fullmatch(r"(?:\+91|91)?[6-9]\d{9}", value.replace(" ", "").replace("-", "")):
            _error(errors, field, value, "Invalid Indian mobile number")
    for field in ("mac_address",):
        value = data.get(field)
        if value:
            compact = re.sub(r"[^0-9A-Fa-f]", "", value)
            if len(compact) != 12:
                _error(errors, field, value, "Invalid MAC address")
            else:
                data[field] = ":".join(compact[i:i+2] for i in range(0, 12, 2)).upper()
    allowed = data.get("allowed_macs")
    if allowed:
        parsed_macs = []
        for value in re.split(r"[,;|\s]+", allowed):
            compact = re.sub(r"[^0-9A-Fa-f]", "", value)
            if len(compact) != 12:
                _error(errors, "allowed_macs", value, "Invalid MAC address")
            else:
                parsed_macs.append(":".join(compact[i:i+2] for i in range(0, 12, 2)).upper())
        data["allowed_macs"] = parsed_macs
    else:
        data["allowed_macs"] = []
    for field in ("ip_address", "nas_ip"):
        value = data.get(field)
        if value:
            try:
                data[field] = str(ipaddress.ip_address(value))
            except ValueError:
                _error(errors, field, value, "Invalid IPv4/IPv6 address")
    for field in ("package_price", "custom_price", "balance_amount", "special_discount", "additional_charges", "wallet_balance_primary", "wallet_balance_secondary", "latitude", "longitude"):
        value = data.get(field)
        if value is not None:
            try:
                data[field] = str(Decimal(value.replace(",", "")))
            except (InvalidOperation, AttributeError):
                _error(errors, field, value, "Invalid decimal value")
    for field, low, high in (("latitude", Decimal("-90"), Decimal("90")), ("longitude", Decimal("-180"), Decimal("180"))):
        if data.get(field) is not None:
            try:
                if not low <= Decimal(data[field]) <= high:
                    _error(errors, field, data[field], f"Must be between {low} and {high}")
            except InvalidOperation:
                pass
    for field in ("expiry_at", "last_renewal_at", "source_added_at", "last_logoff_at", "commitment_date"):
        value = data.get(field)
        if value:
            parsed = None
            for fmt in DATE_FORMATS:
                try:
                    parsed = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                _error(errors, field, value, "Unsupported date format")
            else:
                data[field] = timezone.make_aware(parsed, timezone.get_current_timezone()).isoformat()
    data["source_system"] = SOURCE_SYSTEM
    return data, errors
