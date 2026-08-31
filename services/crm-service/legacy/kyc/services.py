from .models import KycDocument


def has_verified_kyc(customer_id: int) -> bool:
    return KycDocument.objects.filter(
        customer_id=customer_id,
        status=KycDocument.Status.VERIFIED,
        deleted_at__isnull=True,
    ).exists()
