"""Versioned checklist execution rules.

Published checklist versions are immutable; a work order retains the exact
version used. Rules enforce required/conditional/repeatable items, ranges,
allowed values, evidence requirements and blocking/non-blocking failures."""
from __future__ import annotations

import re
from typing import Any

from ..domain.exceptions import ChecklistError
from ..enums import CHECKLIST_ITEM_TYPES
from ..models import ChecklistItem


def validate_item_type(item_type: str) -> str:
    item_type = item_type.upper()
    if item_type not in CHECKLIST_ITEM_TYPES:
        raise ChecklistError(f"invalid checklist item type {item_type!r}")
    return item_type


def validate_response(item: ChecklistItem, value: Any) -> dict:
    """Validate a raw response value against an item's type + constraints.

    Returns the normalized value dict; raises ChecklistError on failure."""
    item_type = item.item_type.upper()
    constraints = item.constraints or {}
    value_type = type(value)

    if item_type in ("CHECKBOX", "YES_NO"):
        if not isinstance(value, (bool, dict)):
            raise ChecklistError(f"item {item.code} requires a boolean/confirmation")
        confirmed = value.get("confirmed") if isinstance(value, dict) else value
        if not isinstance(confirmed, bool):
            raise ChecklistError(f"item {item.code} requires a boolean")
        return {"confirmed": confirmed}
    if item_type in ("TEXT", "SERIAL_NUMBER", "MAC_ADDRESS", "BARCODE_SCAN"):
        text = value.get("value") if isinstance(value, dict) else value
        if text is None:
            raise ChecklistError(f"item {item.code} requires a value")
        text = str(text).strip()
        if item_type == "MAC_ADDRESS":
            if not re.fullmatch(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text):
                raise ChecklistError(f"item {item.code} has an invalid MAC address")
        return {"value": text}
    if item_type == "NUMBER":
        number = float(value.get("value") if isinstance(value, dict) else value)
        minimum = constraints.get("min")
        maximum = constraints.get("max")
        if minimum is not None and number < minimum:
            raise ChecklistError(f"item {item.code} below minimum {minimum}")
        if maximum is not None and number > maximum:
            raise ChecklistError(f"item {item.code} above maximum {maximum}")
        return {"value": number}
    if item_type in ("SELECT", "MULTI_SELECT"):
        allowed = constraints.get("allowed_values", [])
        selected = value.get("value") if isinstance(value, dict) else value
        values = selected if isinstance(selected, list) else [selected]
        if item_type == "MULTI_SELECT" and not isinstance(selected, list):
            raise ChecklistError(f"item {item.code} requires a list for multi-select")
        for v in values:
            if allowed and v not in allowed:
                raise ChecklistError(f"item {item.code} has value {v!r} outside allowed set")
        return {"value": values if item_type == "MULTI_SELECT" else values[0]}
    if item_type in ("PHOTO", "VIDEO", "DOCUMENT", "SIGNATURE"):
        if not isinstance(value, dict) or not value.get("file_ref"):
            raise ChecklistError(f"item {item.code} requires evidence")
        return {"file_ref": value["file_ref"], "checksum": value.get("checksum")}
    if item_type == "GPS_CAPTURE":
        lat = value.get("latitude") if isinstance(value, dict) else None
        lng = value.get("longitude") if isinstance(value, dict) else None
        if lat is None or lng is None:
            raise ChecklistError(f"item {item.code} requires GPS coordinates")
        return {"latitude": lat, "longitude": lng}
    if item_type in ("OPTICAL_READING", "SIGNAL_READING", "SPEED_TEST"):
        result = value.get("value") if isinstance(value, dict) else value
        if result is None:
            raise ChecklistError(f"item {item.code} requires a reading")
        expected_range = constraints.get("expected_range")
        if expected_range and isinstance(result, (int, float)):
            lo, hi = expected_range[0], expected_range[1]
            if not (lo <= result <= hi):
                raise ChecklistError(f"item {item.code} reading {result} outside expected range {expected_range}")
        return {"value": result}
    if item_type == "DATE_TIME":
        return {"value": value.get("value") if isinstance(value, dict) else value}
    raise ChecklistError(f"unsupported item type {item_type!r}")


def item_condition_met(item: ChecklistItem, responses: dict) -> bool:
    """Evaluate a CONDITIONAL item's depends_on rule."""
    rule = item.rule or {}
    if rule.get("type") != "CONDITIONAL":
        return True
    depends_on = rule.get("depends_on")
    when = rule.get("when") or {}
    if not depends_on or depends_on not in responses:
        return False
    response = responses[depends_on]
    if "equals" in when:
        return response.get("value") == when["equals"]
    if "confirmed" in when:
        return response.get("confirmed") is when["confirmed"]
    return True


def validate_checklist(items: list[ChecklistItem], responses: dict) -> tuple[bool, list[str]]:
    """Validate a full submission. Returns (valid, errors)."""
    errors: list[str] = []
    provided = set(responses.keys())
    for item in items:
        if not item_condition_met(item, responses):
            continue
        if item.required and item.code not in provided:
            errors.append(f"required item {item.code} ({item.label}) is missing")
            continue
        if item.code in provided:
            try:
                validate_response(item, responses[item.code])
            except ChecklistError as error:
                errors.append(str(error))
    return (not errors), errors
