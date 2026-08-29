"""Vendor-neutral parameter compilation, diffing and verification.

Profiles contain vendor-neutral logical parameters; compilation resolves them
to device-specific paths using the versioned ParameterMapping catalogue
(TR-098 `InternetGatewayDevice.*` vs TR-181 `Device.*`). Unsupported parameters
are flagged, never silently dropped. Verification compares read-back values
with desired state; sensitive write-only parameters use indirect validation."""
from __future__ import annotations

import re


def _normalize_value(value):
    """Normalize a device read-back value for comparison."""
    if value is None:
        return None
    text = str(value).strip()
    lower = text.lower()
    if lower in ("true", "1", "yes"):
        return True
    if lower in ("false", "0", "no"):
        return False
    try:
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if re.fullmatch(r"-?\d+\.\d+", text):
            return float(text)
    except ValueError:
        pass
    return text


def compile_parameters(mappings: list, profile_parameters: dict, *, data_model_family: str = "TR181") -> dict:
    """Compile vendor-neutral profile parameters into device-specific paths.

    `mappings` is a list of mapping dicts with keys: code, path, read_path,
    data_model_family, writable, mapping_version.
    Returns (compiled, unsupported_codes).
    """
    compiled: dict = {}
    unsupported: list[str] = []
    by_code: dict[str, list] = {}
    for mapping in mappings:
        by_code.setdefault(mapping.get("code"), []).append(mapping)

    for code, value in profile_parameters.items():
        candidates = by_code.get(code, [])
        if not candidates:
            unsupported.append(code)
            continue
        family_match = [m for m in candidates if m.get("data_model_family") == data_model_family]
        selected = family_match[0] if family_match else candidates[0]
        compiled[selected["path"]] = {"value": value, "code": code,
                                      "read_path": selected.get("read_path") or selected["path"],
                                      "writable": selected.get("writable", True)}
    return compiled, unsupported


def normalize_readback(parameters: dict) -> dict:
    return {k: _normalize_value(v) for k, v in (parameters or {}).items()}


def diff_parameters(desired: dict, observed: dict, *, sensitive_codes: list | None = None,
                    sensitive_paths: list | None = None) -> dict:
    """Return {matched, mismatched, missing, sensitive_unreadable}.

    desired: compiled {path: {"value":..., "code":...}}
    observed: raw read-back {path: value}
    """
    observed_norm = normalize_readback(observed)
    sensitive_codes = set(sensitive_codes or [])
    sensitive_paths = set(sensitive_paths or [])
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []
    sensitive_unreadable: list[str] = []

    for path, spec in desired.items():
        code = spec.get("code", path)
        expected = spec.get("value")
        if code in sensitive_codes or path in sensitive_paths:
            # Write-only secret: cannot read back; require indirect confirmation.
            if spec.get("writable"):
                sensitive_unreadable.append(path)
            continue
        if path not in observed_norm:
            missing.append(path)
            continue
        if observed_norm[path] == _normalize_value(expected):
            matched.append(path)
        else:
            mismatched.append(path)
    return {
        "matched": matched,
        "mismatched": mismatched,
        "missing": missing,
        "sensitive_unreadable": sensitive_unreadable,
    }


def verify_configuration(desired: dict, observed: dict, *, sensitive_codes: list | None = None,
                         sensitive_paths: list | None = None,
                         require_readback: bool = True) -> dict:
    """Verification result: verified only when non-sensitive parameters match
    (and read back when require_readback). Unreadable secrets are not drift."""
    diff = diff_parameters(desired, observed, sensitive_codes=sensitive_codes,
                           sensitive_paths=sensitive_paths)
    problems = diff["mismatched"] + diff["missing"]
    verified = len(problems) == 0
    if require_readback and not observed and diff["sensitive_unreadable"] == [] and desired and verified:
        # Nothing observed at all and verification required → not verified.
        verified = False
    return {"verified": verified, **diff}
