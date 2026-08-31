"""Gateway-neutral payment gateway framework."""
from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..enums import GATEWAY_CODES


class GatewayError(Exception):
    def __init__(self, message: str, code: str = "GATEWAY_ERROR", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class GatewayOrder:
    gateway_order_ref: str
    safe_payload: dict
    status: str = "created"


@dataclass
class GatewayResult:
    status: str  # authorized | captured | failed | cancelled | expired | refunded
    external_ref: str
    detail: dict


def sign_payload(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: str, signature: str) -> bool:
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, signature)


class PaymentGateway(ABC):
    code: str = "base"

    @abstractmethod
    def create_payment(self, *, amount: Decimal, currency: str, description: str, idempotency_key: str, return_url: str | None = None, account: Any = None) -> GatewayOrder: ...

    @abstractmethod
    def retrieve_payment(self, gateway_ref: str) -> GatewayResult: ...

    @abstractmethod
    def capture_payment(self, gateway_ref: str, amount: Decimal) -> GatewayResult: ...

    @abstractmethod
    def cancel_payment(self, gateway_ref: str) -> GatewayResult: ...

    @abstractmethod
    def create_refund(self, gateway_ref: str, amount: Decimal, reference: str) -> GatewayResult: ...

    @abstractmethod
    def retrieve_refund(self, gateway_ref: str, refund_ref: str) -> GatewayResult: ...

    @abstractmethod
    def verify_webhook(self, raw_body: str, signature: str, secret: str) -> bool: ...

    @abstractmethod
    def parse_webhook(self, raw_body: str) -> dict: ...

    @abstractmethod
    def fetch_transactions(self) -> list[dict]: ...

    @abstractmethod
    def fetch_settlements(self) -> list[dict]: ...

    @abstractmethod
    def health_check(self) -> bool: ...

    def capabilities(self) -> list[str]:
        return ["hosted_checkout", "refunds", "webhooks"]


_REGISTRY: dict[str, type[PaymentGateway]] = {}


def register(cls: type[PaymentGateway]) -> type[PaymentGateway]:
    _REGISTRY[cls.code] = cls
    return cls


def get_gateway_class(code: str) -> type[PaymentGateway]:
    code = code.upper()
    if code not in GATEWAY_CODES and code not in _REGISTRY:
        raise GatewayError(f"unsupported gateway {code!r}", code="UNSUPPORTED_GATEWAY")
    try:
        return _REGISTRY[code]
    except KeyError as error:
        raise GatewayError(f"gateway adapter not registered: {code}", code="UNSUPPORTED_GATEWAY") from error
