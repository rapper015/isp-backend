from django.db import transaction

from .models import CustomerCodeSequence

CUSTOMER_CODE_PREFIX = "CUS"


def next_customer_code() -> str:
    with transaction.atomic():
        counter, created = CustomerCodeSequence.objects.get_or_create(
            pk=1, defaults={"last_value": 0}
        )
        if not created:
            counter = CustomerCodeSequence.objects.select_for_update().get(pk=1)

        counter.last_value += 1
        counter.save(update_fields=["last_value"])
        return f"{CUSTOMER_CODE_PREFIX}-{counter.last_value:06d}"
