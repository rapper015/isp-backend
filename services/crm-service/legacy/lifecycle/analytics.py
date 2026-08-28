from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from customers.models import Customer

from .models import CustomerLifecycleEvent

ANALYTICS_WINDOW_DAYS = 30


def compute_lifecycle_analytics() -> dict:
    now = timezone.now()
    window_start = now - timedelta(days=ANALYTICS_WINDOW_DAYS)

    customers = Customer.objects.filter(deleted_at__isnull=True)
    total_customers = customers.count()

    by_status = {
        Customer.Status.ONBOARDING: 0,
        Customer.Status.ACTIVE: 0,
        Customer.Status.INACTIVE: 0,
        Customer.Status.SUSPENDED: 0,
        Customer.Status.TERMINATED: 0,
    }
    for row in customers.values("status").annotate(count=Count("id")):
        by_status[row["status"]] = row["count"]

    events_in_window = CustomerLifecycleEvent.objects.filter(created_at__gte=window_start)
    suspensions = events_in_window.filter(to_status=Customer.Status.SUSPENDED).count()
    terminations = events_in_window.filter(to_status=Customer.Status.TERMINATED).count()
    reactivations = events_in_window.filter(
        to_status=Customer.Status.ACTIVE,
        from_status__in=[Customer.Status.SUSPENDED, Customer.Status.INACTIVE],
    ).count()
    new_onboardings = events_in_window.filter(to_status=Customer.Status.ONBOARDING).count()

    churn_rate = (
        round(by_status[Customer.Status.TERMINATED] / total_customers, 4)
        if total_customers
        else 0
    )
    # Of everyone suspended in the window, how many came back - a proxy for
    # "customer stability" the milestone text asks for.
    reactivation_rate = round(reactivations / suspensions, 4) if suspensions else None

    return {
        "generated_at": now,
        "window_days": ANALYTICS_WINDOW_DAYS,
        "total_customers": total_customers,
        "by_status": by_status,
        "churn_rate": churn_rate,
        "window": {
            "new_onboardings": new_onboardings,
            "suspensions": suspensions,
            "terminations": terminations,
            "reactivations": reactivations,
            "reactivation_rate": reactivation_rate,
        },
    }
