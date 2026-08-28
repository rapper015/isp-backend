from datetime import timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from billing.models import BillingAccount, Invoice
from customers.models import Customer
from kyc.services import has_verified_kyc
from subscribers.models import Subscriber

from .models import CustomerLifecycleEvent

SUSPENSION_LOOKBACK_DAYS = 90


def _overdue_invoices_query(now):
    """Overdue = explicitly flagged OVERDUE, or still owing money past due_date
    (covers invoices a background job hasn't flipped to OVERDUE yet)."""
    return Q(status=Invoice.Status.OVERDUE) | (
        Q(balance_due__gt=0)
        & Q(due_date__lt=now)
        & Q(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID])
    )


def compute_risk_profile(customer: Customer) -> dict:
    """Simple rule-based risk score - not ML, just transparent point-scoring
    over the signals already sitting in the DB (overdue billing, suspension
    history, KYC gaps, lapsed subscriptions). Computed on read, nothing
    persisted, so it's always a fresh read of current state.
    """
    now = timezone.now()
    factors = []
    score = 0

    overdue_invoices = Invoice.objects.filter(
        customer=customer, deleted_at__isnull=True
    ).filter(_overdue_invoices_query(now))
    overdue_count = overdue_invoices.count()
    overdue_amount = overdue_invoices.aggregate(total=Sum("balance_due"))["total"] or 0
    if overdue_count:
        points = min(overdue_count * 2, 6)
        score += points
        factors.append(
            {
                "code": "overdue_invoices",
                "description": f"{overdue_count} overdue invoice(s) totalling {overdue_amount}",
                "points": points,
            }
        )

    outstanding_balance = (
        BillingAccount.objects.filter(customer=customer, deleted_at__isnull=True).aggregate(
            total=Sum("outstanding_balance")
        )["total"]
        or 0
    )
    if outstanding_balance > 0:
        score += 1
        factors.append(
            {
                "code": "outstanding_balance",
                "description": f"Outstanding billing balance of {outstanding_balance}",
                "points": 1,
            }
        )

    recent_suspensions = CustomerLifecycleEvent.objects.filter(
        customer=customer,
        to_status=Customer.Status.SUSPENDED,
        created_at__gte=now - timedelta(days=SUSPENSION_LOOKBACK_DAYS),
    ).count()
    if recent_suspensions:
        points = min(recent_suspensions * 2, 4)
        score += points
        factors.append(
            {
                "code": "recent_suspensions",
                "description": (
                    f"{recent_suspensions} suspension(s) in the last "
                    f"{SUSPENSION_LOOKBACK_DAYS} days"
                ),
                "points": points,
            }
        )

    if not has_verified_kyc(customer.id):
        score += 2
        factors.append(
            {
                "code": "kyc_not_verified",
                "description": "No verified KYC document on file",
                "points": 2,
            }
        )

    lapsed_subscribers = Subscriber.objects.filter(
        customer=customer,
        deleted_at__isnull=True,
        status=Subscriber.Status.ACTIVE,
        expires_at__lt=now,
    ).count()
    if lapsed_subscribers:
        score += 1
        factors.append(
            {
                "code": "lapsed_subscriptions",
                "description": f"{lapsed_subscribers} subscription(s) past expiry but still active",
                "points": 1,
            }
        )

    if score >= 6:
        risk_level = "high"
    elif score >= 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "customer": customer.id,
        "score": score,
        "risk_level": risk_level,
        "factors": factors,
        "computed_at": now,
    }
