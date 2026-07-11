"""Duplicate entry prevention for expenses, fuel, EMI, Mithi payments and more."""
from services import sheets_service


def is_duplicate(table, data, fields, exclude_id=None):
    """True when an existing record matches `data` on every field in `fields`."""
    spec = sheets_service.TABLES[table]
    id_field = spec["id"]
    target = {f: str(data.get(f) or "").strip().lower() for f in fields}
    if not any(target.values()):
        return False
    for record in sheets_service.get_all_records(table):
        if exclude_id and record.get(id_field) == exclude_id:
            continue
        if all(str(record.get(f) or "").strip().lower() == target[f] for f in fields):
            return True
    return False


DUPLICATE_RULES = {
    "expenses": ["expense_date", "vehicle_number", "category", "amount", "description"],
    "fuelentries": ["entry_date", "vehicle_number", "litres", "amount"],
    "otheremis": ["emi_name", "lender_name"],
    "mithipayments": ["mithi_person_id", "payment_month"],
    "billing": ["invoice_number", "vehicle_number", "payment_month"],
}


def check(table, data, exclude_id=None):
    """Convenience wrapper using the default rule for a table."""
    fields = DUPLICATE_RULES.get(table)
    if not fields:
        return False
    return is_duplicate(table, data, fields, exclude_id=exclude_id)
