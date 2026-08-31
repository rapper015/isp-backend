import csv
import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from rest_framework.test import APIClient

from accounts.models import AdminUser
from common.passwords import hash_password
from customers.models import Customer,Franchise
from payments.models import Payment
from plans.models import Plan
from subscribers.models import Subscriber

from .importing.parser import HEADER_KEYS,CSVStructureError,read_csv
from .importing.services import commit_import,validate_upload
from .models import Invoice,InvoiceImportBatch,LedgerEntry


HEADERS=list(HEADER_KEYS)
DEFAULT={"Order No":"101","Invoice No":"2950","Status":"Paid","Invoice Type":"Renewal","A/C No":"667","Username":"alice","Customer Name":"Alice","Franchise Name":"Tenant A","Package Name":"Gold","Sub Package":"Months (1)","Payment Type":"Api Transaction","IpAddress":"10.0.0.1","Invoice Date":"01/08/2026 09:27","Due Date":"2026-08-06 12:57:27","Bill From":"02/08/2026 12:57","Bill To":"02/09/2026 12:57","Package Price":"430","Total Tax":"0","Final Invoice Amount":"380","Paid Amount":"380","Current Balance":"0"}


def upload(rows,name="invoices.csv"):
    output=io.StringIO(newline=""); writer=csv.writer(output); writer.writerow(HEADERS)
    for overrides in rows:
        values={**DEFAULT,**overrides}; writer.writerow([values.get(header,"") for header in HEADERS])
    return SimpleUploadedFile(name,output.getvalue().encode("utf-8-sig"),content_type="text/csv")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InvoiceImportTests(TestCase):
    def setUp(self):
        self.franchise=Franchise.objects.create(name="Tenant A",normalized_name="tenant a")
        self.admin=AdminUser.objects.create(name="Admin",email="admin@test.invalid",password_hash=hash_password("secret"),role="super_admin")
        self.plan=Plan.objects.create(plan_code="GOLD",name="Gold",monthly_fee=430,speed_profile_name="gold",download_rate_kbps=1000,upload_rate_kbps=1000,franchise=self.franchise)
        customer=Customer.objects.create(customer_code="C1",full_name="Alice",phone="9876543210",address="A",city="C",franchise=self.franchise)
        self.subscriber=Subscriber.objects.create(subscriber_code="S1",customer=customer,plan=self.plan,username="alice",password_hash="x",installation_address="A",franchise=self.franchise,external_id="667",source_system="legacy_subscriber_csv",static_ip_address="10.0.0.1",current_balance=25)
        self.options={"update_existing":False,"create_missing_packages":False,"dry_run":True}

    def validate(self,rows,**options): return validate_upload(upload(rows),self.franchise,self.admin,{**self.options,**options})

    def test_preview_and_commit_create_connected_financial_records(self):
        batch=self.validate([{}]); self.assertEqual(batch.valid_rows,1); self.assertEqual(Invoice.objects.count(),0)
        commit_import(batch); invoice=Invoice.objects.get()
        self.assertEqual(invoice.subscriber,self.subscriber); self.assertEqual(invoice.customer,self.subscriber.customer); self.assertEqual(invoice.plan,self.plan)
        self.assertEqual(invoice.balance_due,0); self.assertEqual(Payment.objects.get().amount,380); self.assertEqual(LedgerEntry.objects.count(),2)
        self.subscriber.refresh_from_db(); self.assertEqual(self.subscriber.current_balance,25)

    def test_identity_conflict_and_franchise_mismatch_are_errors(self):
        other_customer=Customer.objects.create(customer_code="C2",full_name="Bob",phone="",address="",city="",franchise=self.franchise)
        Subscriber.objects.create(subscriber_code="S2",customer=other_customer,plan=self.plan,username="bob",password_hash="x",installation_address="",franchise=self.franchise,external_id="999",source_system="legacy_subscriber_csv",static_ip_address="10.0.0.2")
        batch=self.validate([{"Username":"bob"}]); self.assertEqual(batch.invalid_rows,1); self.assertIn("different subscribers",str(batch.rows.get().validation_errors))
        self.assertEqual(self.validate([{"Franchise Name":"Other"}]).invalid_rows,1)

    def test_reimport_is_idempotent_and_can_update(self):
        commit_import(self.validate([{}])); second=self.validate([{}]); self.assertEqual(second.rows.get().action,"SKIP")
        commit_import(second); self.assertEqual(Invoice.objects.count(),1)
        update=self.validate([{"Final Invoice Amount":"400","Paid Amount":"200","Status":"Partially Paid"}],update_existing=True); commit_import(update,{"update_existing":True})
        invoice=Invoice.objects.get(); self.assertEqual(invoice.amount,400); self.assertEqual(invoice.balance_due,200); self.assertEqual(Payment.objects.get().amount,200)

    def test_api_permissions_and_missing_headers(self):
        with self.assertRaises(CSVStructureError):
            read_csv(SimpleUploadedFile("bad.csv",b"Invoice No,Username\n1,a"))
        client=APIClient(); self.assertEqual(client.post("/api/v1/invoice-imports/validate/",{},format="multipart").status_code,401)
        client.force_authenticate(user={"userId":self.admin.id,"role":"super_admin"})
        response=client.post("/api/v1/invoice-imports/validate/",{"file":upload([{}]),"franchise_id":self.franchise.id},format="multipart")
        self.assertEqual(response.status_code,201); self.assertEqual(response.data["summary"]["create_count"],1); self.assertTrue(InvoiceImportBatch.objects.exists())
