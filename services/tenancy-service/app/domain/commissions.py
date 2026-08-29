"""Commission calculation engine.

Deterministic, versioned, currency-aware and explainable. Only controlled
calculation types are supported — arbitrary formulas supplied through the
frontend are never executed."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .exceptions import UnsafeRuleError

ALLOWED_TYPES = {
    "FIXED_AMOUNT", "PERCENTAGE", "TIERED_PERCENTAGE", "SLAB", "PER_UNIT_AMOUNT",
    "RECURRING_AMOUNT", "ONE_TIME_BONUS", "THRESHOLD_BONUS", "REVENUE_SPLIT",
    "CONDITIONAL_MULTIPLIER",
}

_ALLOWED_EXCLUSIONS = {
    "TAX", "DISCOUNT", "CREDIT_NOTE", "GATEWAY_FEE", "REFUND", "CHARGEBACK",
    "BAD_DEBT", "INSTALLATION_CHARGE", "SECURITY_DEPOSIT", "PRIOR_PERIOD_ADJUSTMENT",
}


def validate_rule(rule: dict) -> list[str]:
    errors = []
    calc = rule.get("calculation_type")
    if calc not in ALLOWED_TYPES:
        errors.append(f"unsupported calculation type {calc!r}")
    basis = rule.get("basis")
    if not basis:
        errors.append("basis is required")
    if calc in ("PERCENTAGE", "REVENUE_SPLIT") and not (0 < float(rule.get("rate", 0)) <= 100):
        errors.append("rate must be in (0, 100] for percentage splits")
    if calc == "FIXED_AMOUNT" and float(rule.get("fixed_amount", 0)) <= 0:
        errors.append("fixed_amount must be positive")
    exclusions = rule.get("exclusions") or []
    unknown = [e for e in exclusions if e not in _ALLOWED_EXCLUSIONS]
    if unknown:
        errors.append(f"unsupported exclusion {unknown}")
    return errors


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate(rule: dict, *, basis_amount: Decimal, units: Decimal = Decimal("1"),
              tier_key: Decimal | None = None) -> dict:
    """Return {'amount': Decimal, 'formula': str, 'explanation': str}."""
    calc = rule.get("calculation_type")
    rate = Decimal(str(rule.get("rate", 0)))
    raw_fixed = rule.get("fixed_amount")
    fixed = Decimal(str(raw_fixed)) if raw_fixed is not None else Decimal("0")
    amount = Decimal("0")
    formula = f"{calc}(basis={basis_amount})"

    if calc == "FIXED_AMOUNT":
        amount = fixed
        formula = f"fixed_amount={fixed}"
    elif calc == "PERCENTAGE":
        amount = basis_amount * rate / Decimal("100")
        formula = f"{basis_amount} x {rate}%"
    elif calc == "TIERED_PERCENTAGE":
        amount = _tiered(tiers=rule.get("tiers") or [], key=tier_key if tier_key is not None else basis_amount,
                         basis=basis_amount, mode="percentage")
    elif calc == "SLAB":
        amount = _slab(slabs=rule.get("slabs") or [], key=basis_amount)
    elif calc == "PER_UNIT_AMOUNT":
        amount = fixed * units
        formula = f"{fixed} x {units} units"
    elif calc == "RECURRING_AMOUNT":
        amount = basis_amount * rate / Decimal("100")
        formula = f"recurring {basis_amount} x {rate}%"
    elif calc == "ONE_TIME_BONUS":
        amount = fixed
        formula = f"one_time_bonus={fixed}"
    elif calc == "THRESHOLD_BONUS":
        threshold = Decimal(str(rule.get("threshold", 0)))
        amount = fixed if basis_amount >= threshold else Decimal("0")
        formula = f"threshold={threshold}, bonus={fixed}, basis={basis_amount}"
    elif calc == "REVENUE_SPLIT":
        amount = basis_amount * rate / Decimal("100")
        formula = f"revenue_split {basis_amount} x {rate}%"
    elif calc == "CONDITIONAL_MULTIPLIER":
        amount = basis_amount * Decimal(str(rule.get("multiplier", 1)))
        formula = f"{basis_amount} x multiplier={rule.get('multiplier', 1)}"
    else:
        raise UnsafeRuleError(f"unsupported calculation type {calc!r}")

    return {"amount": _round(amount), "formula": formula,
            "explanation": f"{calc} on basis {basis_amount} -> {amount}"}


def _tiered(*, tiers: list, key: Decimal, basis: Decimal, mode: str) -> Decimal:
    total = Decimal("0")
    for tier in sorted(tiers, key=lambda t: float(t.get("min", 0))):
        lower = Decimal(str(tier.get("min", 0)))
        upper = tier.get("max")
        rate = Decimal(str(tier.get("rate", 0)))
        applicable = Decimal("0")
        if upper is None:
            if key >= lower:
                applicable = basis
        elif key >= lower and key < Decimal(str(upper)):
            applicable = basis
        if mode == "percentage":
            total += applicable * rate / Decimal("100")
        else:
            total += applicable * rate
    return total


def _slab(*, slabs: list, key: Decimal) -> Decimal:
    # Slabs are absolute amounts chosen by bracket.
    for slab in sorted(slabs, key=lambda s: float(s.get("min", 0))):
        upper = slab.get("max")
        if upper is None or key < Decimal(str(upper)):
            return Decimal(str(slab.get("amount", 0)))
    return Decimal("0")
