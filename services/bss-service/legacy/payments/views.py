from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView
from aaa.exceptions import AppError
from common.dates import parse_dt

from . import services
from .serializers import PaymentSerializer


class PaymentListCreateView(AdminAPIView):
    def get(self, request):
        payments = services.list_payments()
        return Response(PaymentSerializer(payments, many=True).data)

    def post(self, request):
        data = request.data
        invoice_id = data.get("invoiceId") or data.get("invoice_id")
        amount = data.get("amount")
        method = data.get("method")

        missing = [
            name
            for name, value in (("invoiceId", invoice_id), ("amount", amount), ("method", method))
            if value in (None, "")
        ]
        if missing:
            raise AppError("Validation failed", 400, {"missingFields": missing})

        payment = services.record_payment(
            {
                "invoice_id": invoice_id,
                "amount": amount,
                "method": method,
                "received_at": parse_dt(data.get("receivedAt") or data.get("received_at")),
                "notes": data.get("notes"),
            }
        )
        return Response(PaymentSerializer(payment).data, status=HTTP_201_CREATED)


class PaymentDetailView(AdminAPIView):
    def get(self, request, payment_id):
        payment = services.get_payment(payment_id)
        return Response(PaymentSerializer(payment).data)

    def delete(self, request, payment_id):
        services.delete_payment(payment_id)
        return Response(status=HTTP_204_NO_CONTENT)
