from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response

from aaa.models import AccountingRecord, ActiveSession
from accounts.views import AdminAPIView
from billing.models import Invoice
from customers.models import Customer
from subscribers.models import Subscriber


class DashboardOverviewView(AdminAPIView):
    def get(self, request):
        now = timezone.now()
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        total_customers = Customer.objects.filter(deleted_at__isnull=True).count()
        active_subscribers = Subscriber.objects.filter(
            status=Subscriber.Status.ACTIVE, deleted_at__isnull=True
        ).count()
        online_users = ActiveSession.objects.filter(status=ActiveSession.Status.ONLINE).count()

        monthly_revenue = (
            Invoice.objects.filter(created_at__gte=month_start).aggregate(total=Sum("amount"))[
                "total"
            ]
            or Decimal("0")
        )
        outstanding_revenue = (
            Invoice.objects.aggregate(total=Sum("balance_due"))["total"] or Decimal("0")
        )
        usage = AccountingRecord.objects.aggregate(
            upload_bytes=Sum("input_octets"), download_bytes=Sum("output_octets")
        )

        return Response(
            {
                "total_customers": total_customers,
                "active_subscribers": active_subscribers,
                "online_users": online_users,
                "active_sessions": online_users,
                "monthly_revenue": monthly_revenue,
                "outstanding_revenue": outstanding_revenue,
                "usage": {
                    "upload_bytes": usage["upload_bytes"] or 0,
                    "download_bytes": usage["download_bytes"] or 0,
                },
            }
        )
