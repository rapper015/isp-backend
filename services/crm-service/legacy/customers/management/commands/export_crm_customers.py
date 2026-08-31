"""One-time, resumable exporter from the legacy Django database to CRM."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError

from customers.models import Customer


class Command(BaseCommand):
    help = "Export legacy customers to crm-service without duplicating customer codes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        base_url = os.environ.get("CRM_SERVICE_URL", "http://crm-service:8000").rstrip("/")
        customers = Customer.objects.filter(deleted_at__isnull=True).order_by("id")
        if options["limit"]:
            customers = customers[: options["limit"]]

        exported = skipped = 0
        for customer in customers:
            payload = {
                "customer_code": customer.customer_code,
                "full_name": customer.full_name,
                "phone": customer.phone,
                "email": customer.email or None,
                "address": customer.address or None,
                "city": customer.city or None,
            }
            if options["dry_run"]:
                self.stdout.write(f"would export {customer.customer_code}")
                continue
            try:
                urlopen(f"{base_url}/customers/by-code/{customer.customer_code}", timeout=10)
                skipped += 1
                continue
            except HTTPError as exc:
                if exc.code != 404:
                    raise CommandError(f"CRM lookup failed for {customer.customer_code}: {exc}") from exc
            request = Request(
                f"{base_url}/customers",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(request, timeout=10)
                exported += 1
            except HTTPError as exc:
                raise CommandError(f"CRM export failed for {customer.customer_code}: {exc.read().decode()}") from exc
        self.stdout.write(self.style.SUCCESS(f"exported={exported} skipped={skipped}"))
