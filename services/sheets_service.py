"""Central data service: table definitions, init, generic CRUD, IDs, audit logs, cache.

All business tables use TEXT columns with generated string IDs (e.g. VEH-0001)
so records stay simple, portable and audit-friendly. Numeric parsing happens
in Python via to_float().
"""
from datetime import datetime

from services.db import execute, fetch_all, fetch_one

# ---------------------------------------------------------------- table schema

TABLES = {
    "vehicles": {
        "id": "vehicle_id", "prefix": "VEH",
        "columns": [
            "vehicle_id", "vehicle_number", "vehicle_type", "default_driver",
            "vehicle_status", "rc_number", "rc_expiry", "insurance_policy_number",
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
            "exit_date", "assigned_vehicle", "created_date", "updated_date",
        ],
    },
    "expenses": {
        "id": "expense_id", "prefix": "EXP",
        "columns": [
            "expense_id", "expense_date", "for_month", "expense_for",
            "vehicle_number", "driver_name", "category", "sub_category",
            "description", "amount", "payment_mode", "paid_by", "created_date",
        ],
    },
    "documents": {
        "id": "document_id", "prefix": "DOC",
        "columns": [
            "document_id", "entity_type", "entity_id", "document_type",
            "file_name", "drive_url", "uploaded_date",
        ],
    },
    "settings": {
        "id": "key", "prefix": "SET",
        "columns": ["key", "value"],
    },
    "auditlogs": {
        "id": "log_id", "prefix": "LOG",
        "columns": [
            "log_id", "action", "module", "entity_id", "details",
            "user_email", "timestamp",
        ],
    },
    "gstpurchases": {
        "id": "purchase_id", "prefix": "GSTP",
        "columns": [
            "purchase_id", "invoice_date", "invoice_number", "company_name",
            "amount", "sgst", "cgst", "total_amount", "description", "created_date",
        ],
    },
    "purse": {
        "id": "purse_id", "prefix": "PRS",
        "columns": [
            "purse_id", "date", "holder", "type", "amount", "description",
            "vehicle_number", "category", "reference_id", "created_date",
        ],
    },
    "users": {
        "id": "user_id", "prefix": "USR",
        "columns": [
            "user_id", "email", "name", "role", "status",
            "created_date", "updated_date",
        ],
    },
    "income": {
        "id": "income_id", "prefix": "INC",
        "columns": [
            "income_id", "income_date", "source", "amount", "description",
            "created_date",
        ],
    },
    "billing": {
        "id": "bill_id", "prefix": "BIL",
        "columns": [
            "bill_id", "invoice_number", "invoice_date", "payment_month",
            "vehicle_number", "fixed_amount", "variable_amount", "traffic_challan",
            "tollgates", "sub_total", "sgst", "cgst", "tds", "total_amount",
            "payment_status", "paid_amount", "balance_amount", "description",
            "invoice_description", "created_date", "updated_date",
        ],
    },
    "receivables": {
        "id": "receivable_id", "prefix": "RCV",
        "columns": [
            "receivable_id", "receive_date", "payment_month", "bill_id", "amount",
            "payment_mode", "reference_number", "description", "created_date",
        ],
    },
    "fuelentries": {
        "id": "fuel_id", "prefix": "FUEL",
        "columns": [
            "fuel_id", "entry_date", "vehicle_number", "driver_name", "fuel_type",
            "litres", "amount", "kilometre", "fuel_station", "payment_mode",
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
    "incentives": {
        "id": "incentive_id", "prefix": "INCV",
        "columns": [
            "incentive_id", "driver_id", "driver_name", "for_month", "entry_type",
            "amount", "description", "created_date",
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
}

# ------------------------------------------------------------------ init/cache

_cache = {}


def clear_cache(table=None):
    if table:
        _cache.pop(table, None)
    else:
        _cache.clear()


def init_tables():
    """Create all PostgreSQL tables if they do not exist."""
    for name, spec in TABLES.items():
        cols = ", ".join(f'"{c}" TEXT' for c in spec["columns"])
        pk = spec["id"]
        execute(f'CREATE TABLE IF NOT EXISTS {name} ({cols}, PRIMARY KEY ("{pk}"))')


# ------------------------------------------------------------------- helpers

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def this_month():
    return datetime.now().strftime("%Y-%m")


def to_float(value):
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def generate_id(table):
    """Generate the next sequential ID for a table, e.g. VEH-0001."""
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


# ------------------------------------------------------------------- generic CRUD

def get_all_records(table, use_cache=True):
    if use_cache and table in _cache:
        return _cache[table]
    rows = fetch_all(f"SELECT * FROM {table}")
    _cache[table] = rows
    return rows


def find_record_by_id(table, record_id):
    spec = TABLES[table]
    return fetch_one(f'SELECT * FROM {table} WHERE "{spec["id"]}" = %s', (record_id,))


def add_record(table, data):
    """Insert a record; unknown keys are dropped, ID auto-generated if missing."""
    spec = TABLES[table]
    record = {k: ("" if v is None else str(v)) for k, v in data.items() if k in spec["columns"]}
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


def delete_record(table, record_id):
    spec = TABLES[table]
    execute(f'DELETE FROM {table} WHERE "{spec["id"]}" = %s', (record_id,))
    clear_cache(table)
    return True


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
