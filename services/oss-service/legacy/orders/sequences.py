from django.utils import timezone

from billing.sequences import _next_value


def next_order_number() -> str:
    period = timezone.now().strftime("%Y%m")
    value = _next_value("ORD", period)
    return f"ORD-{period}-{value:06d}"
