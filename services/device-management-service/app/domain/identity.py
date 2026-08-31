"""Device identity normalization and tenant-resolution rules.

TR-069 does not inherently provide safe platform tenant ownership. Resolution
uses explicit, evidence-based strategies; ambiguous devices remain quarantined
and a device owned by one tenant can never be claimed by another without an
authorized transfer workflow."""
from __future__ import annotations

import re

from ..enums import TENANT_RESOLUTION_RESULTS


def normalize_oui(value: str | None) -> str:
    """Normalize an OUI to uppercase hex without separators (6 hex digits)."""
    if not value:
        return ""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", str(value)).upper()
    if len(cleaned) < 6:
        return cleaned
    return cleaned[:6]


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", str(value)).upper()
    if len(cleaned) != 12:
        return None
    return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))


def normalize_serial(value: str | None) -> str:
    if not value:
        return ""
    return str(value).strip()


def acs_identity_key(oui: str, product_class: str | None, serial: str) -> tuple[str, str, str]:
    """Primary ACS identity: (OUI, product class, serial). Never a MAC or name."""
    return (normalize_oui(oui), (product_class or "").strip().upper(), normalize_serial(serial))


def resolve_outcome(confidence: float | None, matched: bool, conflicting: bool, blocked: bool = False) -> str:
    """Classify a tenant-resolution result deterministically."""
    if blocked:
        return "BLOCKED"
    if not matched:
        return "UNKNOWN"
    if conflicting or (confidence is not None and confidence < 0.6):
        return "AMBIGUOUS"
    return "MATCHED"


def validate_claim_result(result: str) -> str:
    if result not in TENANT_RESOLUTION_RESULTS:
        raise ValueError(f"invalid tenant-resolution result {result!r}")
    return result
