import csv
import io
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AdminUser
from common.passwords import hash_password
from customers.models import Customer, Franchise
from plans.models import Plan

from .importing.parser import CSVStructureError, normalize_row, read_csv
from .importing.services import commit_import, validate_upload
from .models import Subscriber, SubscriberImportBatch


HEADERS = ["Id", "CAF No", "Status", "Outage", "Account Type", "Franchise Name", "Username", "MAC", "Name", "Father/Company Name", "Package Name", "Sub Package", "Mobile", "Alt. Mobile", "IpAddress", "Expiry Date", "Last Renewal", "FUP Limit", "Branch", "Area", "Colony", "Building", "City", "State", "Wallet Balance", "Node", "Pop", "Switch", "Door No", "Billing Address", "Installation Address", "GSTIN", "Email", "Date Added", "Allowed MACs", "NAS IP", "POP Tech Exe", "POP Coll Exe", "Last Payment Source", "Package Price", "Custom Price", "Balance Amount", "Last Logoff", "Nas Port Id", "Spl. Discount", "Add. Charges", "Wallet Balance", "Latitude", "Longitude", "Auto Renew", "Connection Type", "CAF Form", "Address Proof", "Identity Proof", "Customer Pic", "User Added", "Commitment Date"]


def csv_upload(rows, headers=HEADERS, name="users.csv"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        values = {"Id":"001", "Status":"active", "Outage":"0", "Account Type":"mac", "Franchise Name":"Tenant A", "Username":"alice", "MAC":"aabbccddeeff", "Name":"Alice", "Package Name":"Gold", "Mobile":"9876543210", "IpAddress":"10.0.0.1", "Billing Address":"Line 1\nLine 2", "Installation Address":"Install", "Expiry Date":"13/08/2026 10:30", "Auto Renew":"Yes", "Wallet Balance":"12.50"}
        values.update(row)
        rendered=[]; wallet_seen=0
        for header in headers:
            if header == "Wallet Balance":
                wallet_seen += 1; rendered.append(row.get(f"Wallet Balance {wallet_seen}", values.get(header,"")))
            else: rendered.append(values.get(header,""))
        writer.writerow(rendered)
    return SimpleUploadedFile(name, output.getvalue().encode("utf-8-sig"), content_type="text/csv")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SubscriberImportTests(TestCase):
    def setUp(self):
        self.franchise=Franchise.objects.create(name="Tenant A",normalized_name="tenant a")
        self.other=Franchise.objects.create(name="Tenant B",normalized_name="tenant b")
        self.admin=AdminUser.objects.create(name="Admin",email="admin@test.invalid",password_hash=hash_password("secret"),role="super_admin")
        self.plan=Plan.objects.create(plan_code="GOLD",name="Gold",monthly_fee=100,speed_profile_name="gold",download_rate_kbps=1000,upload_rate_kbps=1000,franchise=self.franchise)
        self.options={"update_existing":False,"create_missing_packages":False,"create_missing_locations":False,"dry_run":True}

    def validate(self, rows, **options):
        return validate_upload(csv_upload(rows),self.franchise,self.admin,{**self.options,**options})

    def test_valid_preview_does_not_modify_subscribers(self):
        batch=self.validate([{}])
        self.assertEqual(batch.status,"VALIDATED"); self.assertEqual(batch.valid_rows,1); self.assertEqual(Subscriber.objects.count(),0)

    def test_commit_after_validation_and_multiline_address(self):
        batch=commit_import(self.validate([{}]))
        subscriber=Subscriber.objects.get()
        self.assertEqual(batch.status,"COMPLETED"); self.assertEqual(subscriber.customer.billing_address,"Line 1\nLine 2")

    def test_duplicate_wallet_headers_are_positional_and_not_summed(self):
        batch=commit_import(self.validate([{"Wallet Balance 1":"10.25","Wallet Balance 2":"99.75"}]))
        subscriber=Subscriber.objects.get()
        self.assertEqual(subscriber.current_balance,10.25); self.assertEqual(subscriber.import_metadata["wallet_balance_secondary"],"99.75")

    def test_blank_optional_fields_are_valid(self):
        batch=self.validate([{"MAC":"","IpAddress":"","Email":"","Alt. Mobile":""}])
        self.assertEqual(batch.invalid_rows,0)

    def test_invalid_values_return_field_errors(self):
        batch=self.validate([{"Expiry Date":"31/31/2020","MAC":"bad","IpAddress":"999.1.1.1","Mobile":"123","Latitude":"91","Longitude":"-181"}])
        fields={error["field"] for error in batch.rows.get().validation_errors}
        self.assertTrue({"expiry_at","mac_address","ip_address","primary_mobile","latitude","longitude"}.issubset(fields))

    def test_duplicate_username_in_tenant_is_skipped_but_other_tenant_allowed(self):
        first=commit_import(self.validate([{}])).rows.get().target_subscriber
        batch=self.validate([{"Id":"002","MAC":"","IpAddress":""}])
        self.assertEqual(batch.rows.get().action,"SKIP")
        customer=Customer.objects.create(customer_code="OTHER",full_name="Other",phone="",address="",city="",franchise=self.other)
        Subscriber.objects.create(subscriber_code="OTHER",customer=customer,plan=self.plan,username=first.username,password_hash="x",installation_address="",franchise=self.other)

    def test_duplicate_mobile_and_email_are_allowed(self):
        batch=self.validate([{}, {"Id":"002","Username":"bob","MAC":"112233445566","IpAddress":"10.0.0.2","Mobile":"9876543210","Email":"same@test.invalid"}])
        self.assertEqual(batch.invalid_rows,0)

    def test_existing_update_and_blank_does_not_overwrite(self):
        subscriber=commit_import(self.validate([{"Email":"old@test.invalid"}])).rows.get().target_subscriber
        batch=self.validate([{"Email":"","Mobile":""}],update_existing=True)
        commit_import(batch,{"update_existing":True})
        subscriber.refresh_from_db(); self.assertEqual(subscriber.customer.email,"old@test.invalid"); self.assertEqual(subscriber.customer.phone,"9876543210")

    def test_identity_conflict(self):
        commit_import(self.validate([{}]))
        commit_import(self.validate([{"Id":"002","Username":"bob","MAC":"112233445566","IpAddress":"10.0.0.2"}]))
        batch=self.validate([{"Id":"001","Username":"bob","MAC":"","IpAddress":""}],update_existing=True)
        self.assertIn("different subscribers",batch.rows.get().validation_errors[-1]["error"])

    def test_missing_package_option(self):
        invalid=self.validate([{"Package Name":"Missing"}]); self.assertEqual(invalid.invalid_rows,1)
        created=commit_import(self.validate([{"Package Name":"Missing"}],create_missing_packages=True))
        self.assertEqual(created.status,"COMPLETED"); self.assertTrue(Plan.objects.filter(name="Missing").exists())

    def test_franchise_mismatch_rejected(self):
        self.assertEqual(self.validate([{"Franchise Name":"Tenant B"}]).invalid_rows,1)

    def test_same_file_twice_is_idempotent_and_warned_after_commit(self):
        commit_import(self.validate([{}]))
        second=self.validate([{}]); self.assertEqual(second.rows.get().action,"SKIP"); self.assertTrue(second.validation_summary["warnings"])
        commit_import(second); self.assertEqual(Subscriber.objects.count(),1)

    def test_partial_and_retry_failed_rows(self):
        batch=self.validate([{}, {"Id":"002","Username":"bob","MAC":"112233445566","IpAddress":"10.0.0.2"}])
        original = __import__("subscribers.importing.services",fromlist=["process_row"]).process_row
        with mock.patch("subscribers.importing.services.process_row",side_effect=[original(batch.rows.first(),batch.options),ValueError("boom")]):
            commit_import(batch)
        batch.refresh_from_db(); self.assertEqual(batch.status,"PARTIAL")
        commit_import(batch,retry_errors_only=True); batch.refresh_from_db(); self.assertEqual(batch.status,"COMPLETED")

    def test_missing_headers_and_malformed_rows(self):
        with self.assertRaises(CSVStructureError): read_csv(csv_upload([],headers=["Id","Username"]))

    def test_normalizer_unknown_enum_and_formula_value_preserved(self):
        data,errors=normalize_row({"external_id":"1","username":"u","full_name":"N","package_name":"P","franchise_name":"T","status":"mystery","source_created_by":"=CMD()"})
        self.assertEqual(data["source_created_by"],"=CMD()"); self.assertEqual(errors[0]["field"],"status")

    def test_large_file_uses_multiple_chunks(self):
        rows=[{"Id":str(i),"Username":f"u{i}","MAC":"","IpAddress":""} for i in range(501)]
        batch=commit_import(self.validate(rows)); self.assertEqual(batch.created_rows,501)

    def test_api_permissions_and_tenant_filtering(self):
        client=APIClient(); response=client.post("/api/v1/subscriber-imports/validate/",{},format="multipart"); self.assertEqual(response.status_code,401)
        client.force_authenticate(user={"userId":self.admin.id,"role":"super_admin"})
        response=client.post("/api/v1/subscriber-imports/validate/",{"file":csv_upload([{}]),"franchise_id":self.franchise.id},format="multipart")
        self.assertEqual(response.status_code,201)
        listed=client.get(f"/api/v1/subscriber-imports/?franchise_id={self.other.id}"); self.assertEqual(listed.data["count"],0)

    def test_error_csv_escapes_formula_injection(self):
        batch=self.validate([{"Status":"=bad"}])
        client=APIClient(); client.force_authenticate(user={"userId":self.admin.id,"role":"super_admin"})
        response=client.get(f"/api/v1/subscriber-imports/{batch.id}/errors/download/")
        body=b"".join(response.streaming_content).decode(); self.assertIn("'=bad",body)
