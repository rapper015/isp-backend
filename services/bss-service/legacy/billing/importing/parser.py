import csv
import io
import ipaddress
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone


SOURCE_SYSTEM = "legacy_invoice_csv"
REQUIRED_HEADERS = {"Invoice No", "Username", "Franchise Name", "Package Name", "Invoice Date", "Due Date", "Bill From", "Bill To", "Final Invoice Amount", "Paid Amount"}
DATE_FORMATS = ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d")
STATUS_MAP = {
    "paid": "paid", "partial": "partially_paid", "partially paid": "partially_paid", "partial paid": "partially_paid",
    "unpaid": "issued", "issued": "issued", "overdue": "overdue", "draft": "draft",
    "cancelled": "cancelled", "canceled": "cancelled", "void": "void",
}
HEADER_KEYS = {
    "Order No":"order_number", "Invoice No":"invoice_number", "Status":"status", "Invoice Type":"invoice_type",
    "Renew Type":"renew_type", "User Type":"user_type", "Ref No":"reference_number", "A/C No":"account_number",
    "Username":"username", "Customer Name":"customer_name", "Mobile":"mobile", "Billing Address":"billing_address",
    "Installation Address":"installation_address", "Zip":"zip", "Franchise Name":"franchise_name", "Entity Code":"entity_code",
    "GSTIN":"gstin", "Branch":"branch", "Package Name":"package_name", "Sub Package":"sub_package",
    "Payment Type":"payment_type", "IpAddress":"ip_address", "Renewed By":"renewed_by", "Assign To":"assigned_to",
    "Renew Date":"renewed_at", "Invoice Date":"invoice_date", "Due Date":"due_date", "Expiry Date":"expiry_date",
    "Bill From":"billing_period_start", "Bill To":"billing_period_end", "Comment":"comment", "Last Paid Date":"last_paid_at",
    "Area":"area", "Colony":"colony", "Node":"node", "Pop":"pop", "Addon Package":"addon_package",
    "Package Price":"package_price", "Addon Pkg Amt":"addon_amount", "Pkg. Adjustment":"package_adjustment",
    "Adj.Refund":"adjustment_refund", "Tax":"tax", "Swatchh Bharat Cess":"swachh_bharat_cess",
    "Krishi Kalyan Cess":"krishi_kalyan_cess", "CGST":"cgst", "SGST":"sgst", "IGST":"igst", "Total Tax":"total_tax",
    "Tax Type":"tax_type", "Discount":"discount", "Spl. Discount":"special_discount", "Add. Charges":"additional_charges",
    "Gen. Add Charges":"generated_additional_charges", "Pre. Credit":"previous_credit", "Pre. Bal":"previous_balance",
    "Previous Inv. Balance":"previous_invoice_balance", "Installation Charges":"installation_charges",
    "Final Invoice Amount":"amount", "Paid Amount":"paid_amount", "Current Balance":"current_balance",
}
DECIMAL_FIELDS = {
    "package_price", "addon_amount", "package_adjustment", "adjustment_refund", "tax", "swachh_bharat_cess",
    "krishi_kalyan_cess", "cgst", "sgst", "igst", "total_tax", "discount", "special_discount",
    "additional_charges", "generated_additional_charges", "previous_credit", "previous_balance",
    "previous_invoice_balance", "installation_charges", "amount", "paid_amount", "current_balance",
}
DATE_FIELDS = {"renewed_at", "invoice_date", "due_date", "expiry_date", "billing_period_start", "billing_period_end", "last_paid_at"}


class CSVStructureError(ValueError): pass


def read_csv(upload):
    upload.seek(0); raw = upload.read()
    try: text = raw if isinstance(raw, str) else raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc: raise CSVStructureError("File must be UTF-8 or UTF-8 with BOM") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try: headers = [h.strip() for h in next(reader)]
    except (StopIteration, csv.Error) as exc: raise CSVStructureError("CSV header is missing or malformed") from exc
    missing = sorted(REQUIRED_HEADERS - set(headers))
    if missing: raise CSVStructureError(f"Missing required columns: {', '.join(missing)}")
    duplicates = {h for h in headers if headers.count(h) > 1}
    if duplicates: raise CSVStructureError(f"Duplicate headers: {', '.join(sorted(duplicates))}")
    keys = [HEADER_KEYS.get(h, f"unmapped__{h}") for h in headers]; rows=[]
    try:
        for number, values in enumerate(reader, start=2):
            if len(values) != len(headers):
                rows.append((number, {}, [{"field":"row","value":"","error":f"Expected {len(headers)} columns, got {len(values)}"}]))
            else: rows.append((number, {keys[i]:values[i] for i in range(len(keys))}, []))
    except csv.Error as exc: raise CSVStructureError(f"Malformed CSV near row {reader.line_num}: {exc}") from exc
    return headers, keys, rows


def normalize_row(raw):
    data={k:(v.strip() if isinstance(v,str) else v) for k,v in raw.items()}; errors=[]
    data={k:(None if v=="" else v) for k,v in data.items()}
    def error(field, message): errors.append({"field":field,"value":data.get(field) or "","error":message})
    for field in ("invoice_number","username","franchise_name","package_name","invoice_date","due_date","billing_period_start","billing_period_end","amount","paid_amount"):
        if not data.get(field): error(field,"This field is required")
    if data.get("status"):
        mapped=STATUS_MAP.get(data["status"].casefold())
        if mapped: data["status"]=mapped
        else: error("status","Unknown invoice status")
    else: data["status"]="issued"
    if data.get("ip_address"):
        try: data["ip_address"]=str(ipaddress.ip_address(data["ip_address"]))
        except ValueError: error("ip_address","Invalid IPv4/IPv6 address")
    for field in DECIMAL_FIELDS:
        value=data.get(field)
        if value is not None:
            try: data[field]=str(Decimal(str(value).replace(",", ""))) if str(value).strip() not in ("-","NO") else "0"
            except InvalidOperation: error(field,"Invalid decimal value")
    for field in DATE_FIELDS:
        value=data.get(field)
        if value:
            parsed=None
            for fmt in DATE_FORMATS:
                try: parsed=datetime.strptime(value,fmt); break
                except ValueError: pass
            if parsed is None: error(field,"Unsupported date format")
            else: data[field]=timezone.make_aware(parsed,timezone.get_current_timezone()).isoformat()
    try:
        if Decimal(data.get("amount") or 0) < 0: error("amount","Must not be negative")
        if Decimal(data.get("paid_amount") or 0) < 0: error("paid_amount","Must not be negative")
        if Decimal(data.get("paid_amount") or 0) > Decimal(data.get("amount") or 0): error("paid_amount","Cannot exceed final invoice amount")
    except InvalidOperation: pass
    data["source_system"]=SOURCE_SYSTEM
    return data,errors
