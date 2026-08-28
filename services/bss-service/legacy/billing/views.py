from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.dates import parse_dt

from . import services
from .serializers import (
    BillingAccountSerializer,
    BillingSettingsSerializer,
    InvoiceSerializer,
    LedgerEntrySerializer,
)


def _require_dt(data: dict, *keys: str):
    for key in keys:
        parsed = parse_dt(data.get(key))
        if parsed is not None:
            return parsed
    raise AppError("Validation failed", 400, {"missingFields": [keys[0]]})


class InvoiceListCreateView(AdminAPIView):
    def get(self, request):
        invoices = services.list_invoices()
        return Response(InvoiceSerializer(invoices, many=True).data)

    def post(self, request):
        data = request.data
        subscriber_id = data.get("subscriberId") or data.get("subscriber_id")
        if not subscriber_id:
            raise AppError("Validation failed", 400, {"missingFields": ["subscriberId"]})

        invoice = services.generate_invoice(
            {
                "subscriber_id": subscriber_id,
                "billing_period_start": _require_dt(data, "billingPeriodStart", "billing_period_start"),
                "billing_period_end": _require_dt(data, "billingPeriodEnd", "billing_period_end"),
                "due_date": parse_dt(data.get("dueDate") or data.get("due_date")),
                "notes": data.get("notes"),
            }
        )
        return Response(InvoiceSerializer(invoice).data, status=HTTP_201_CREATED)


class GenerateDueInvoicesView(AdminAPIView):
    def post(self, request):
        data = request.data
        result = services.generate_due_invoices(
            run_date=parse_dt(data.get("runDate") or data.get("run_date")),
            subscriber_ids=data.get("subscriberIds") or data.get("subscriber_ids"),
        )
        result["invoices"] = InvoiceSerializer(result["invoices"], many=True).data
        return Response(result)


class RefreshOverdueInvoicesView(AdminAPIView):
    def post(self, request):
        return Response(services.refresh_overdue_invoices())


class InvoiceDetailView(AdminAPIView):
    def get(self, request, invoice_id):
        invoice = services.get_invoice_by_id(invoice_id)
        return Response(InvoiceSerializer(invoice).data)

    def patch(self, request, invoice_id):
        data = request.data
        payload = {}
        if "status" in data:
            payload["status"] = data["status"]
        if "dueDate" in data or "due_date" in data:
            payload["due_date"] = parse_dt(data.get("dueDate") or data.get("due_date"))
        if "notes" in data:
            payload["notes"] = data["notes"]

        invoice = services.update_invoice(invoice_id, payload)
        return Response(InvoiceSerializer(invoice).data)

    def delete(self, request, invoice_id):
        services.delete_invoice(invoice_id)
        return Response(status=HTTP_204_NO_CONTENT)


class LedgerListView(AdminAPIView):
    def get(self, request):
        entries = services.list_ledger()
        return Response(LedgerEntrySerializer(entries, many=True).data)


class LedgerEntryDetailView(AdminAPIView):
    def delete(self, request, entry_id):
        services.delete_ledger_entry(entry_id)
        return Response(status=HTTP_204_NO_CONTENT)


class AdjustmentCreateView(AdminAPIView):
    def post(self, request):
        data = request.data
        adjustment_type = data.get("adjustmentType") or data.get("adjustment_type")
        amount = data.get("amount")
        description = data.get("description")

        missing = [
            name
            for name, value in (
                ("adjustmentType", adjustment_type),
                ("amount", amount),
                ("description", description),
            )
            if value in (None, "")
        ]
        if missing:
            raise AppError("Validation failed", 400, {"missingFields": missing})

        ledger_entry = services.create_adjustment(
            {
                "invoice_id": data.get("invoiceId") or data.get("invoice_id"),
                "billing_account_id": data.get("billingAccountId") or data.get("billing_account_id"),
                "subscriber_id": data.get("subscriberId") or data.get("subscriber_id"),
                "adjustment_type": adjustment_type,
                "amount": amount,
                "description": description,
                "notes": data.get("notes"),
                "posted_at": parse_dt(data.get("postedAt") or data.get("posted_at")),
            }
        )
        return Response(LedgerEntrySerializer(ledger_entry).data, status=HTTP_201_CREATED)


class BillingSettingsView(AdminAPIView):
    def get(self, request):
        return Response(BillingSettingsSerializer(services.get_settings()).data)

    def patch(self, request):
        settings_obj = services.update_settings(request.data)
        return Response(BillingSettingsSerializer(settings_obj).data)


class BillingAccountListView(AdminAPIView):
    def get(self, request):
        accounts = services.list_accounts()
        return Response(BillingAccountSerializer(accounts, many=True).data)


class BillingAccountDetailView(AdminAPIView):
    def delete(self, request, account_id):
        services.delete_billing_account(account_id)
        return Response(status=HTTP_204_NO_CONTENT)


class BillingAccountPlanView(AdminAPIView):
    def patch(self, request, account_id):
        data = request.data
        plan_id = data.get("planId") or data.get("plan_id")
        if not plan_id:
            raise AppError("Validation failed", 400, {"missingFields": ["planId"]})

        account = services.update_account_plan(
            account_id,
            plan_id,
            sync_subscriber_plan=bool(
                data.get("syncSubscriberPlan") or data.get("sync_subscriber_plan")
            ),
        )
        return Response(BillingAccountSerializer(account).data)
