"""Money handling for the BSS revenue domain.

Never use binary floating point for money. All amounts are `Decimal` with an
explicit currency. Cross-currency allocation is forbidden. Minor-unit
representation is centralised here (INR uses paise = 1/100)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .enums import CURRENCIES

MINOR_UNITS = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2}


class MoneyError(ValueError):
    pass


def normalize_currency(currency: str | None) -> str:
    if not currency:
        return "INR"
    currency = currency.upper()
    if currency not in CURRENCIES:
        raise MoneyError(f"unsupported currency {currency!r}")
    return currency


def money(value) -> Decimal:
    """Coerce to a currency-safe Decimal with 2 decimal places (no float)."""
    if isinstance(value, float):
        raise MoneyError("binary float is not allowed for money; pass Decimal or string")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise MoneyError(f"invalid money value {value!r}") from error


def to_minor_units(value) -> int:
    """Convert a Decimal amount to integer minor units (e.g. paise)."""
    return int(money(value) * (10 ** MINOR_UNITS[normalize_currency(None)])) if False else int(money(value) * 100)


@dataclass(frozen=True)
class MoneyAmount:
    amount: Decimal
    currency: str

    def __post_init__(self):
        object.__setattr__(self, "amount", money(self.amount))
        object.__setattr__(self, "currency", normalize_currency(self.currency))

    def __add__(self, other: "MoneyAmount") -> "MoneyAmount":
        self._check(other)
        return MoneyAmount(self.amount + other.amount, self.currency)

    def __sub__(self, other: "MoneyAmount") -> "MoneyAmount":
        self._check(other)
        return MoneyAmount(self.amount - other.amount, self.currency)

    def _check(self, other: "MoneyAmount") -> None:
        if self.currency != other.currency:
            raise MoneyError(f"cross-currency operation not allowed: {self.currency} vs {other.currency}")
