"""Central data service: schema, auto-migration, generic CRUD, IDs, audit, cache.

TABLES is the single source of truth for the database shape. Adding a table key
or a column to an existing list is all that is needed - init_tables() creates
missing tables and ALTERs missing columns on every boot, so deployments
self-migrate.

All business columns are TEXT; numbers and dates are parsed in Python via
to_float()/parse_date(). Every table's first column is its primary key.
Times are IST (Asia/Kolkata) - never use UTC for date defaults.
"""
from datetime import datetime, timedelta, timezone

from services.db import execute, fetch_all, fetch_one

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- table schema

TABLES = {
    "vehicles": {
        "id": "vehicle_id", "prefix": "VEH",
        "columns": [
            "vehicle_id", "vehicle_number", "vehicle_type", "default_driver",
            "default_vendor", "vehicle_status", "monthly_salary",
            "rc_number", "rc_expiry", "insurance_policy_number",
            "insurance_company", "insurance_start_date", "insurance_expiry_date",
            "permit_number", "permit_expiry_date", "fitness_expiry_date",
            "puc_expiry_date", "loan_available", "bank_name", "loan_account_number",
            "emi_amount", "emi_date", "loan_start_date", "loan_end_date",
            "created_date", "updated_date",
        ],
    },
    "drivers": {
        "id": "driver_id", "prefix": "DRV",
        "columns": [
            "driver_id", "employee_type", "driver_name", "email", "mobile_number",
            "emergency_contact", "address", "aadhaar_number",
            "driving_license_number", "license_expiry_date", "bank_name",
            "account_number", "ifsc_code", "salary", "joining_date", "status",
            "exit_date", "assigned_vehicle", "portal_pin",
            "created_date", "updated_date",
        ],
    },
    "vehicleassignments": {
        "id": "assignment_id", "prefix": "ASG",
        "columns": [
            "assignment_id", "vehicle_id", "vehicle_number", "driver_id",
            "driver_name", "start_date", "end_date", "created_date", "updated_date",
        ],
    },
    "attendance": {
        "id": "attendance_id", "prefix": "ATT",
        "columns": [
            "attendance_id", "date", "driver_id", "driver_name", "status",
            "marked_by", "created_date",
        ],
    },
    "incentives": {
        "id": "incentive_id", "prefix": "INCV",
        "columns": [
            "incentive_id", "driver_id", "driver_name", "for_month", "amount",
            "description", "entered_by", "created_date", "updated_date",
        ],
    },
    "expenses": {
        "id": "expense_id", "prefix": "EXP",
        "columns": [
            "expense_id", "expense_date", "for_month", "expense_for",
            "vehicle_number", "driver_name", "category", "sub_category",
            "description", "amount", "payment_mode", "paid_by", "bank_account",
            "created_date", "updated_date",
        ],
    },
    "bankaccounts": {
        "id": "account_id", "prefix": "BANK",
        "columns": [
            "account_id", "bank_name", "account_label", "account_number",
            "ifsc_code", "branch", "account_type", "opening_balance",
            "opening_date", "colour", "status", "notes",
            "created_date", "updated_date",
        ],
    },
    "banktransactions": {
        "id": "txn_id", "prefix": "BTX",
        "columns": [
            "txn_id", "txn_date", "account_id", "bank_name", "type", "amount",
            "category", "description", "reference_number", "payment_mode",
            "to_account_id", "holder", "reference_id", "created_date",
        ],
    },
    "fuelentries": {
        "id": "fuel_id", "prefix": "FUEL",
        "columns": [
            "fuel_id", "entry_date", "vehicle_number", "driver_name", "fuel_type",
            "litres", "amount", "kilometre", "fuel_station", "payment_mode",
            "payment_status", "paid_date", "created_date",
        ],
    },
    "vendors": {
        "id": "vendor_id", "prefix": "VEN",
        "columns": [
            "vendor_id", "vendor_name", "contact_person", "mobile_number", "email",
            "address", "gst_number", "payment_terms", "status",
            "created_date", "updated_date",
        ],
    },
    "billing": {
        "id": "bill_id", "prefix": "BIL",
        "columns": [
            "bill_id", "invoice_number", "bill_type", "invoice_date",
            "payment_month", "working_month", "vehicle_number", "vendor_name",
            "fixed_amount", "variable_amount", "traffic_challan", "tollgates",
            "sub_total", "sgst", "cgst", "tds", "total_amount",
            "payment_status", "paid_amount", "balance_amount", "description",
            "invoice_description", "created_date", "updated_date",
        ],
    },
    "receivables": {
        "id": "receivable_id", "prefix": "RCV",
        "columns": [
            "receivable_id", "receive_date", "payment_month", "bill_id",
            "vendor_name", "amount", "payment_mode", "reference_number",
            "description", "created_date",
        ],
    },
    "income": {
        "id": "income_id", "prefix": "INC",
        "columns": [
            "income_id", "income_date", "vehicle_number", "driver_name",
            "vendor_name", "trip_from", "trip_to", "material", "quantity", "unit",
            "rate", "amount", "invoice_number", "payment_status", "payment_date",
            "description", "created_date",
        ],
    },
    "outsidevehicles": {
        "id": "ov_id", "prefix": "OV",
        "columns": [
            "ov_id", "vehicle_number", "owner_name", "mobile_number", "bank_name",
            "account_number", "ifsc_code", "status", "created_date", "updated_date",
        ],
    },
    "outsidetransactions": {
        "id": "trans_id", "prefix": "OTX",
        "columns": [
            "trans_id", "date", "for_month", "vehicle_number", "owner_name",
            "type", "category", "description", "amount", "payment_mode",
            "created_date",
        ],
    },
    "otheremis": {
        "id": "emi_id", "prefix": "EMI",
        "columns": [
            "emi_id", "emi_name", "category", "description", "vehicle_number",
            "lender_name", "total_amount", "down_payment", "emi_amount",
            "emi_date", "start_date", "end_date", "total_installments",
            "paid_installments", "status", "created_date", "updated_date",
        ],
    },
    "purse": {
        "id": "purse_id", "prefix": "PRS",
        "columns": [
            "purse_id", "date", "holder", "type", "amount", "description",
            "vehicle_number", "category", "reference_id", "created_date",
        ],
    },
    "gstpurchases": {
        "id": "purchase_id", "prefix": "GSTP",
        "columns": [
            "purchase_id", "invoice_date", "invoice_number", "company_name",
            "amount", "sgst", "cgst", "total_amount", "description",
            "credit_to", "created_date",
        ],
    },
    "mithipersons": {
        "id": "mithi_person_id", "prefix": "MP",
        "columns": [
            "mithi_person_id", "person_name", "mobile_number", "alternate_mobile",
            "address", "aadhaar_number", "pan_number", "main_amount", "mithi_rate",
            "monthly_mithi_amount", "start_date", "main_amount_returned",
            "returned_date", "status", "notes", "created_date", "updated_date",
        ],
    },
    "mithipayments": {
        "id": "mithi_payment_id", "prefix": "MPY",
        "columns": [
            "mithi_payment_id", "mithi_person_id", "person_name", "payment_month",
            "payment_date", "mithi_amount", "payment_mode", "reference_number",
            "notes", "created_date",
        ],
    },
    "mileagereports": {
        "id": "mileage_id", "prefix": "MIL",
        "columns": [
            "mileage_id", "month", "vehicle_number", "approved_km", "litres",
            "updated_by", "updated_date",
        ],
    },
    "documents": {
        "id": "document_id", "prefix": "DOC",
        "columns": [
            "document_id", "entity_type", "entity_id", "document_type",
            "file_name", "drive_url", "uploaded_date",
        ],
    },
    "users": {
        "id": "user_id", "prefix": "USR",
        "columns": [
            "user_id", "email", "name", "role", "status",
            "created_date", "updated_date",
        ],
    },
    "settings": {
        "id": "key", "prefix": "SET",
        "columns": ["key", "value", "updated_date"],
    },
    "auditlogs": {
        "id": "log_id", "prefix": "LOG",
        "columns": [
            "log_id", "action", "module", "entity_id", "details",
            "user_email", "timestamp",
        ],
    },
}

# binary document storage lives outside TABLES (it holds BYTEA, not TEXT)
DOCUMENT_FILES_DDL = """
CREATE TABLE IF NOT EXISTS document_files (
    document_id TEXT PRIMARY KEY,
    entity_type TEXT,
    entity_id TEXT,
    doc_type TEXT,
    file_name TEXT,
    file_data BYTEA,
    mime_type TEXT,
    uploaded_date TEXT
)
"""

# ------------------------------------------------------------------ init/cache

_cache = {}
CACHE_TTL_SECONDS = 5  # short-lived: keeps a page's many reads cheap without
                       # ever serving balances that another worker has changed


def clear_cache(table=None):
    if table:
        _cache.pop(table, None)
    else:
        _cache.clear()


invalidate_cache = clear_cache  # blueprint alias


def init_tables():
    """Create missing tables and add missing columns (auto-migration)."""
    for name, spec in TABLES.items():
        cols = ", ".join(f'"{c}" TEXT' for c in spec["columns"])
        execute(f'CREATE TABLE IF NOT EXISTS {name} ({cols}, PRIMARY KEY ("{spec["id"]}"))')
        existing = {r["column_name"] for r in fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s", (name,))}
        for col in spec["columns"]:
            if col not in existing:
                execute(f'ALTER TABLE {name} ADD COLUMN IF NOT EXISTS "{col}" TEXT')
    execute(DOCUMENT_FILES_DDL)
    clear_cache()


# ------------------------------------------------------------------- helpers

def now_str():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(IST).strftime("%Y-%m-%d")


def this_month():
    return datetime.now(IST).strftime("%Y-%m")


def shift_month(month, delta):
    """Shift a 'YYYY-MM' string by delta months."""
    try:
        year, mon = (int(x) for x in str(month).split("-")[:2])
    except (ValueError, TypeError):
        return month
    idx = year * 12 + (mon - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_name(month):
    try:
        return datetime.strptime(str(month)[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month or ""


def to_float(value):
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_date(value):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value or "").strip()[:10], fmt)
        except ValueError:
            continue
    return None


def generate_id(table):
    """Next sequential ID for a table, e.g. VEH-0001."""
    spec = TABLES[table]
    rows = fetch_all(f'SELECT "{spec["id"]}" AS id FROM {table}')
    highest = 0
    for row in rows:
        raw = str(row["id"] or "")
        if "-" in raw:
            tail = raw.rsplit("-", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
    return f"{spec['prefix']}-{highest + 1:04d}"


gen_id = generate_id  # blueprint alias


# ------------------------------------------------------------------- generic CRUD

def get_all_records(table, use_cache=True):
    if use_cache and table in _cache:
        cached_at, rows = _cache[table]
        if (datetime.now(IST) - cached_at).total_seconds() < CACHE_TTL_SECONDS:
            return rows
    rows = fetch_all(f"SELECT * FROM {table}")
    _cache[table] = (datetime.now(IST), rows)
    return rows


def find_record_by_id(table, record_id):
    spec = TABLES[table]
    return fetch_one(f'SELECT * FROM {table} WHERE "{spec["id"]}" = %s', (record_id,))


find_row_by_id = find_record_by_id  # blueprint alias


def add_record(table, data):
    """Insert a record; unknown keys dropped, ID and timestamps auto-filled."""
    spec = TABLES[table]
    record = {k: ("" if v is None else str(v)) for k, v in data.items()
              if k in spec["columns"]}
    if not record.get(spec["id"]):
        record[spec["id"]] = generate_id(table)
    if "created_date" in spec["columns"] and not record.get("created_date"):
        record["created_date"] = now_str()
    if "updated_date" in spec["columns"] and not record.get("updated_date"):
        record["updated_date"] = now_str()
    cols = ", ".join(f'"{c}"' for c in record)
    marks = ", ".join(["%s"] * len(record))
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(record.values()))
    clear_cache(table)
    return record[spec["id"]]


append_row = add_record  # blueprint alias


def update_record(table, record_id, data):
    spec = TABLES[table]
    updates = {k: ("" if v is None else str(v)) for k, v in data.items()
               if k in spec["columns"] and k != spec["id"]}
    if "updated_date" in spec["columns"]:
        updates["updated_date"] = now_str()
    if not updates:
        return False
    sets = ", ".join(f'"{c}" = %s' for c in updates)
    execute(f'UPDATE {table} SET {sets} WHERE "{spec["id"]}" = %s',
            tuple(updates.values()) + (record_id,))
    clear_cache(table)
    return True


update_row = update_record  # blueprint alias


def delete_record(table, record_id):
    spec = TABLES[table]
    execute(f'DELETE FROM {table} WHERE "{spec["id"]}" = %s', (record_id,))
    clear_cache(table)
    return True


delete_row = delete_record  # blueprint alias


def get_setting(key, default=""):
    row = fetch_one('SELECT "value" FROM settings WHERE "key" = %s', (key,))
    return (row or {}).get("value") or default


def set_setting(key, value):
    execute('INSERT INTO settings ("key", "value", "updated_date") VALUES (%s, %s, %s) '
            'ON CONFLICT ("key") DO UPDATE SET "value" = EXCLUDED."value", '
            '"updated_date" = EXCLUDED."updated_date"',
            (key, str(value), now_str()))
    clear_cache("settings")


# ------------------------------------------------------------------- audit log

def add_audit_log(action, module, entity_id, details, user_email):
    try:
        add_record("auditlogs", {
            "action": action,
            "module": module,
            "entity_id": str(entity_id or ""),
            "details": str(details or "")[:500],
            "user_email": user_email or "",
            "timestamp": now_str(),
        })
    except Exception:
        pass  # audit logging must never break the main action
