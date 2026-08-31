from django.db import transaction
from django.utils import timezone

from .models import SequenceCounter


def _next_value(prefix: str, period: str) -> int:
    with transaction.atomic():
        counter, created = SequenceCounter.objects.get_or_create(
            prefix=prefix, period=period, defaults={"last_value": 0}
        )
        if not created:
            counter = SequenceCounter.objects.select_for_update().get(pk=counter.pk)

        counter.last_value += 1
        counter.save(update_fields=["last_value"])
        return counter.last_value


def next_invoice_number(prefix: str) -> str:
    period = timezone.now().strftime("%Y%m")
    value = _next_value(prefix, period)
    return f"{prefix}-{period}-{value:06d}"


def next_payment_reference() -> str:
    period = timezone.now().strftime("%Y%m")
    value = _next_value("PAY", period)
    return f"PAY-{period}-{value:06d}"
