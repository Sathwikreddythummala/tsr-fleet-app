"""Billing: vendor invoices, GST/TDS, multi-vehicle invoices and receivables.

Working Month is the month the vehicle worked; Payment Month is when it is
billed and collected (defaults to working month + 1). An invoice number can
cover several vehicle bills - a GST override on such an invoice is shared
across its bills in proportion to each sub total.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.exports import excel_response
from utils.filters import filter_multi
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["billing"])

BILL_TYPES = ("Monthly Rental", "Trip Based", "Supplementary", "Other")
PAID_TOLERANCE = 10.0  # a balance of ₹10 or less counts as fully paid


def _rates():
    from routes.company import get_profile
    profile = get_profile()
    return (ss.to_float(profile.get("sgst_rate")) / 100 or 0.09,
            ss.to_float(profile.get("cgst_rate")) / 100 or 0.09,
            ss.to_float(profile.get("tds_rate")) / 100 or 0.02)


@router.get("/billing")
async def billing_page(request: Request, user=Depends(require_view)):
    return render(request, "billing.html", {"bill_types": list(BILL_TYPES)})


@router.get("/api/billing/list")
async def list_bills(vehicle: str = "", status: str = "", vendor: str = "",
                     payment_month: str = "", working_month: str = "",
                     user=Depends(require_view)):
    rows = ss.get_all_records("billing")
    rows = filter_multi(rows, "vehicle_number", vehicle)
    rows = filter_multi(rows, "payment_status", status, default="Pending")
    rows = filter_multi(rows, "vendor_name", vendor)
    if payment_month:
        rows = [b for b in rows if b.get("payment_month") == payment_month]
    if working_month:
        rows = [b for b in rows if b.get("working_month") == working_month]
    rows.sort(key=lambda b: (b.get("invoice_date") or "",
                             b.get("invoice_number") or ""), reverse=True)
    for row in rows:
        row["payment_month_name"] = ss.month_name(row.get("payment_month"))
        row["working_month_name"] = ss.month_name(row.get("working_month"))
    return {
        "rows": rows,
        "total_billed": round(sum(ss.to_float(b.get("total_amount")) for b in rows), 2),
        "total_paid": round(sum(ss.to_float(b.get("paid_amount")) for b in rows), 2),
        "total_balance": round(sum(ss.to_float(b.get("balance_amount")) for b in rows), 2),
    }


@router.get("/api/billing/invoices")
async def bills_grouped_by_invoice(vendor: str = "", pending_only: int = 0,
                                   user=Depends(require_view)):
    groups = {}
    for b in ss.get_all_records("billing"):
        if vendor and b.get("vendor_name") != vendor:
            continue
        inv = b.get("invoice_number") or "NO-INVOICE"
        g = groups.setdefault(inv, {
            "invoice_number": inv, "invoice_date": b.get("invoice_date"),
            "vendor_name": b.get("vendor_name", ""),
            "working_month": b.get("working_month", ""),
            "payment_month": b.get("payment_month", ""),
            "invoice_description": b.get("invoice_description", ""),
            "bills": [], "total": 0.0, "paid": 0.0, "balance": 0.0,
        })
        g["bills"].append(b)
        g["total"] += ss.to_float(b.get("total_amount"))
        g["paid"] += ss.to_float(b.get("paid_amount"))
        g["balance"] += ss.to_float(b.get("balance_amount"))
    rows = sorted(groups.values(), key=lambda g: g.get("invoice_date") or "",
                  reverse=True)
    if pending_only:
        rows = [g for g in rows if g["balance"] > PAID_TOLERANCE]
    for g in rows:
        g["total"] = round(g["total"], 2)
        g["paid"] = round(g["paid"], 2)
        g["balance"] = round(g["balance"], 2)
        g["working_month_name"] = ss.month_name(g["working_month"])
        g["payment_month_name"] = ss.month_name(g["payment_month"])
        g["vehicle_count"] = len(g["bills"])
    return rows


@router.post("/api/billing/add")
async def add_bill(request: Request, user=Depends(require_editor)):
    data = _calculate(await request.json())
    if not data.get("invoice_number"):
        data["invoice_number"] = _next_invoice_number()
    data.setdefault("invoice_date", ss.today_str())
    data.setdefault("working_month", ss.shift_month(ss.this_month(), -1))
    data.setdefault("payment_month", ss.shift_month(data["working_month"], 1))
    bill_id = ss.add_record("billing", data)
    ss.add_audit_log("Create", "Billing", bill_id,
                     f"Bill {data['invoice_number']} {data.get('vehicle_number')}"
                     f" ₹{data.get('total_amount')}", user["email"])
    return {"success": True, "id": bill_id, "invoice_number": data["invoice_number"]}


@router.post("/api/billing/add-invoice")
async def add_invoice(request: Request, user=Depends(require_editor)):
    """Create one invoice covering several vehicle line items."""
    payload = await request.json()
    lines = payload.get("lines") or []
    if not lines:
        raise HTTPException(status_code=400, detail="Add at least one vehicle line")
    invoice_number = payload.get("invoice_number") or _next_invoice_number()
    working_month = payload.get("working_month") or ss.shift_month(ss.this_month(), -1)
    payment_month = payload.get("payment_month") or ss.shift_month(working_month, 1)
    invoice_date = payload.get("invoice_date") or ss.today_str()

    prepared = []
    for line in lines:
        row = dict(line)
        row.update({
            "invoice_number": invoice_number, "invoice_date": invoice_date,
            "working_month": working_month, "payment_month": payment_month,
            "vendor_name": payload.get("vendor_name", ""),
            "bill_type": payload.get("bill_type", ""),
            "invoice_description": payload.get("invoice_description", ""),
        })
        prepared.append(_calculate(row))

    # distribute invoice-level GST/TDS overrides proportionally by sub total
    subtotal = sum(ss.to_float(r["sub_total"]) for r in prepared) or 1.0
    for field in ("sgst", "cgst", "tds"):
        override = payload.get(field)
        if str(override or "").strip() == "":
            continue
        total_override = ss.to_float(override)
        for row in prepared:
            share = ss.to_float(row["sub_total"]) / subtotal
            row[field] = round(total_override * share, 2)
        _retotal(prepared)

    created = [ss.add_record("billing", row) for row in prepared]
    ss.add_audit_log("Create", "Billing", invoice_number,
                     f"Invoice {invoice_number} with {len(created)} vehicle line(s)",
                     user["email"])
    return {"success": True, "invoice_number": invoice_number, "ids": created}


@router.put("/api/billing/{bill_id}")
async def update_bill(bill_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    bill = ss.find_record_by_id("billing", bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    merged = {**bill, **data}
    ss.update_record("billing", bill_id, _calculate(merged))
    _recalculate_bill(bill_id)
    ss.add_audit_log("Update", "Billing", bill_id, "Updated bill", user["email"])
    return {"success": True}


@router.put("/api/billing/invoice/{invoice_number}/description")
async def update_invoice_description(invoice_number: str, request: Request,
                                     user=Depends(require_editor)):
    data = await request.json()
    updated = 0
    for b in ss.get_all_records("billing"):
        if b.get("invoice_number") == invoice_number:
            ss.update_record("billing", b["bill_id"],
                             {"invoice_description": data.get("invoice_description", "")})
            updated += 1
    return {"success": True, "updated": updated}


@router.delete("/api/billing/{bill_id}")
async def delete_bill(bill_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("billing", bill_id):
        raise HTTPException(status_code=404, detail="Bill not found")
    for r in ss.get_all_records("receivables"):
        if r.get("bill_id") == bill_id:
            ss.delete_record("receivables", r["receivable_id"])
    ss.delete_record("billing", bill_id)
    ss.add_audit_log("Delete", "Billing", bill_id, "Deleted bill and its payments",
                     user["email"])
    return {"success": True}


@router.get("/api/billing/export/excel")
async def export_bills(vehicle: str = "", status: str = "", vendor: str = "",
                       payment_month: str = "", working_month: str = "",
                       user=Depends(require_view)):
    data = await list_bills(vehicle, status, vendor, payment_month, working_month, user)
    headers = ["Invoice", "Date", "Working Month", "Payment Month", "Customer",
               "Vehicle", "Sub Total", "SGST", "CGST", "TDS", "Total", "Paid",
               "Balance", "Status"]
    rows = [[b.get("invoice_number"), b.get("invoice_date"),
             b.get("working_month_name"), b.get("payment_month_name"),
             b.get("vendor_name"), b.get("vehicle_number"),
             ss.to_float(b.get("sub_total")), ss.to_float(b.get("sgst")),
             ss.to_float(b.get("cgst")), ss.to_float(b.get("tds")),
             ss.to_float(b.get("total_amount")), ss.to_float(b.get("paid_amount")),
             ss.to_float(b.get("balance_amount")), b.get("payment_status")]
            for b in data["rows"]]
    totals = ["", "", "", "", "", "", "", "", "", "TOTAL", data["total_billed"],
              data["total_paid"], data["total_balance"], ""]
    return excel_response("Billing", headers, rows, "billing.xlsx", totals)


# --------------------------------------------------------------- receivables

@router.get("/api/billing/receivables")
async def list_receivables(bill_id: str = "", vendor: str = "",
                           user=Depends(require_view)):
    rows = ss.get_all_records("receivables")
    if bill_id:
        rows = [r for r in rows if r.get("bill_id") == bill_id]
    if vendor:
        rows = [r for r in rows if r.get("vendor_name") == vendor]
    rows.sort(key=lambda r: r.get("receive_date") or "", reverse=True)
    return {"rows": rows,
            "total": round(sum(ss.to_float(r.get("amount")) for r in rows), 2)}


@router.post("/api/billing/receivables")
async def add_receivable(request: Request, user=Depends(require_editor)):
    data = await request.json()
    amount = ss.to_float(data.get("amount"))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    data.setdefault("receive_date", ss.today_str())
    data.setdefault("payment_month", (data.get("receive_date") or "")[:7])
    invoice_number = data.pop("invoice_number", "")

    if data.get("bill_id"):
        bill = ss.find_record_by_id("billing", data["bill_id"])
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")
        data["vendor_name"] = bill.get("vendor_name", "")
        receivable_id = ss.add_record("receivables", data)
        _recalculate_bill(data["bill_id"])
        ss.add_audit_log("Payment added", "Billing", receivable_id,
                         f"₹{amount} against {bill.get('invoice_number')}", user["email"])
        return {"success": True, "id": receivable_id}

    if invoice_number:
        return _split_invoice_payment(invoice_number, amount, data, user)
    raise HTTPException(status_code=400, detail="Choose a bill or an invoice")


@router.put("/api/billing/receivables/{receivable_id}")
async def update_receivable(receivable_id: str, request: Request,
                            user=Depends(require_editor)):
    data = await request.json()
    record = ss.find_record_by_id("receivables", receivable_id)
    if not record:
        raise HTTPException(status_code=404, detail="Payment not found")
    ss.update_record("receivables", receivable_id, data)
    _recalculate_bill(data.get("bill_id") or record.get("bill_id"))
    if record.get("bill_id") and record.get("bill_id") != data.get("bill_id"):
        _recalculate_bill(record.get("bill_id"))
    return {"success": True}


@router.delete("/api/billing/receivables/{receivable_id}")
async def delete_receivable(receivable_id: str, user=Depends(require_editor)):
    record = ss.find_record_by_id("receivables", receivable_id)
    if not record:
        raise HTTPException(status_code=404, detail="Payment not found")
    ss.delete_record("receivables", receivable_id)
    _recalculate_bill(record.get("bill_id"))
    ss.add_audit_log("Delete", "Billing", receivable_id, "Deleted payment", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ internals

def _calculate(data):
    """Fill sub total, SGST, CGST, TDS, total, balance and status."""
    sgst_rate, cgst_rate, tds_rate = _rates()
    fixed = ss.to_float(data.get("fixed_amount"))
    variable = ss.to_float(data.get("variable_amount"))
    challan = ss.to_float(data.get("traffic_challan"))
    tolls = ss.to_float(data.get("tollgates"))
    sub_total = fixed + variable

    def resolve(field, rate):
        raw = data.get(field)
        return (ss.to_float(raw) if str(raw or "").strip() != ""
                else round(sub_total * rate, 2))

    sgst = resolve("sgst", sgst_rate)
    cgst = resolve("cgst", cgst_rate)
    tds = resolve("tds", tds_rate)
    total = round(sub_total + sgst + cgst - tds + challan + tolls, 2)
    paid = ss.to_float(data.get("paid_amount"))
    balance = round(total - paid, 2)
    data.update({
        "sub_total": sub_total, "sgst": sgst, "cgst": cgst, "tds": tds,
        "total_amount": total, "paid_amount": paid, "balance_amount": balance,
        "payment_status": _status(total, paid),
    })
    if data.get("working_month") and not data.get("payment_month"):
        data["payment_month"] = ss.shift_month(data["working_month"], 1)
    return data


def _retotal(rows):
    for row in rows:
        total = round(ss.to_float(row["sub_total"]) + ss.to_float(row["sgst"])
                      + ss.to_float(row["cgst"]) - ss.to_float(row["tds"])
                      + ss.to_float(row.get("traffic_challan"))
                      + ss.to_float(row.get("tollgates")), 2)
        paid = ss.to_float(row.get("paid_amount"))
        row["total_amount"] = total
        row["balance_amount"] = round(total - paid, 2)
        row["payment_status"] = _status(total, paid)


def _status(total, paid):
    if total > 0 and (total - paid) <= PAID_TOLERANCE:
        return "Paid"
    return "Partial" if paid > 0 else "Pending"


def _next_invoice_number():
    from routes.company import get_profile
    prefix = get_profile().get("invoice_prefix") or "INV"
    stamp = ss.this_month().replace("-", "")
    existing = {b.get("invoice_number") or "" for b in ss.get_all_records("billing")}
    seq = 1
    while f"{prefix}-{stamp}-{seq:03d}" in existing:
        seq += 1
    return f"{prefix}-{stamp}-{seq:03d}"


def _recalculate_bill(bill_id):
    """Recompute a bill's paid amount, balance and status from its payments."""
    if not bill_id:
        return
    bill = ss.find_record_by_id("billing", bill_id)
    if not bill:
        return
    paid = sum(ss.to_float(r.get("amount")) for r in ss.get_all_records("receivables")
               if r.get("bill_id") == bill_id)
    total = ss.to_float(bill.get("total_amount"))
    ss.update_record("billing", bill_id, {
        "paid_amount": round(paid, 2),
        "balance_amount": round(total - paid, 2),
        "payment_status": _status(total, paid),
    })


def _split_invoice_payment(invoice_number, amount, data, user):
    """Spread an invoice-level payment across that invoice's bills, oldest first."""
    bills = [b for b in ss.get_all_records("billing")
             if b.get("invoice_number") == invoice_number
             and ss.to_float(b.get("balance_amount")) > 0]
    if not bills:
        raise HTTPException(status_code=400,
                            detail="No unpaid bills found for this invoice")
    bills.sort(key=lambda b: b.get("invoice_date") or "")
    remaining = amount
    created = []
    for bill in bills:
        if remaining <= 0:
            break
        portion = min(remaining, ss.to_float(bill.get("balance_amount")))
        created.append(ss.add_record("receivables", {
            **data, "bill_id": bill["bill_id"],
            "vendor_name": bill.get("vendor_name", ""),
            "amount": round(portion, 2)}))
        _recalculate_bill(bill["bill_id"])
        remaining -= portion
    ss.add_audit_log("Payment added", "Billing", invoice_number,
                     f"₹{amount} split across {len(created)} bill(s)", user["email"])
    return {"success": True, "ids": created,
            "unallocated": round(max(0.0, remaining), 2)}
