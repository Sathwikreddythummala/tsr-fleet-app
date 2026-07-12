"""Billing module: bills, invoice numbers, GST/TDS calculation, receivables."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_admin, require_admin_or_viewer
from utils.templates import render

router = APIRouter(tags=["billing"])

SGST_RATE = 0.09
CGST_RATE = 0.09
TDS_RATE = 0.02


@router.get("/billing")
async def billing_page(request: Request, user=Depends(require_admin_or_viewer)):
    return render(request, "billing.html")


def _split(value):
    """Parse a comma-separated multi-select filter value into a list."""
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@router.get("/api/billing")
async def list_bills(vehicle: str = "", status: str = "", month: str = "",
                     user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("billing")
    vehicles = _split(vehicle)
    if vehicles:
        rows = [b for b in rows if b.get("vehicle_number") in vehicles]
    statuses = _split(status)
    if statuses:
        rows = [b for b in rows if (b.get("payment_status") or "Pending") in statuses]
    if month:
        rows = [b for b in rows if b.get("payment_month") == month]
    rows = sorted(rows, key=lambda b: b.get("invoice_date") or "", reverse=True)
    return {
        "rows": rows,
        "total_billed": sum(ss.to_float(b.get("total_amount")) for b in rows),
        "total_paid": sum(ss.to_float(b.get("paid_amount")) for b in rows),
        "total_balance": sum(ss.to_float(b.get("balance_amount")) for b in rows),
    }


@router.get("/api/billing/invoices")
async def bills_grouped_by_invoice(user=Depends(require_admin_or_viewer)):
    groups = {}
    for b in ss.get_all_records("billing"):
        inv = b.get("invoice_number") or "NO-INVOICE"
        g = groups.setdefault(inv, {
            "invoice_number": inv, "invoice_date": b.get("invoice_date"),
            "invoice_description": b.get("invoice_description", ""),
            "bills": [], "total": 0, "paid": 0, "balance": 0,
        })
        g["bills"].append(b)
        g["total"] += ss.to_float(b.get("total_amount"))
        g["paid"] += ss.to_float(b.get("paid_amount"))
        g["balance"] += ss.to_float(b.get("balance_amount"))
    return sorted(groups.values(), key=lambda g: g.get("invoice_date") or "", reverse=True)


@router.post("/api/billing")
async def add_bill(request: Request, user=Depends(require_admin)):
    data = await request.json()
    data = _calculate(data)
    if not data.get("invoice_number"):
        data["invoice_number"] = _next_invoice_number()
    data.setdefault("invoice_date", ss.today_str())
    data.setdefault("payment_month", ss.this_month())
    bill_id = ss.add_record("billing", data)
    ss.add_audit_log("Create", "Billing", bill_id,
                     f"Bill {data['invoice_number']} ₹{data.get('total_amount')}", user["email"])
    return {"success": True, "id": bill_id, "invoice_number": data["invoice_number"]}


@router.put("/api/billing/{bill_id}")
async def update_bill(bill_id: str, request: Request, user=Depends(require_admin)):
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
                                     user=Depends(require_admin)):
    data = await request.json()
    for b in ss.get_all_records("billing"):
        if b.get("invoice_number") == invoice_number:
            ss.update_record("billing", b["bill_id"],
                             {"invoice_description": data.get("invoice_description", "")})
    return {"success": True}


@router.delete("/api/billing/{bill_id}")
async def delete_bill(bill_id: str, user=Depends(require_admin)):
    if not ss.find_record_by_id("billing", bill_id):
        raise HTTPException(status_code=404, detail="Bill not found")
    for r in ss.get_all_records("receivables"):
        if r.get("bill_id") == bill_id:
            ss.delete_record("receivables", r["receivable_id"])
    ss.delete_record("billing", bill_id)
    ss.add_audit_log("Delete", "Billing", bill_id, "Deleted bill and its receivables",
                     user["email"])
    return {"success": True}


# --------------------------------------------------------------- receivables

@router.get("/api/receivables")
async def list_receivables(bill_id: str = "", user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("receivables")
    if bill_id:
        rows = [r for r in rows if r.get("bill_id") == bill_id]
    return sorted(rows, key=lambda r: r.get("receive_date") or "", reverse=True)


@router.post("/api/receivables")
async def add_receivable(request: Request, user=Depends(require_admin)):
    data = await request.json()
    amount = ss.to_float(data.get("amount"))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    data.setdefault("receive_date", ss.today_str())

    invoice_number = data.pop("invoice_number", "")
    if data.get("bill_id"):
        bill = ss.find_record_by_id("billing", data["bill_id"])
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")
        rid = ss.add_record("receivables", data)
        _recalculate_bill(data["bill_id"])
        ss.add_audit_log("Payment added", "Billing", rid,
                         f"₹{amount} against {data['bill_id']}", user["email"])
        return {"success": True, "id": rid}

    if invoice_number:
        return _split_invoice_payment(invoice_number, amount, data, user)
    raise HTTPException(status_code=400, detail="bill_id or invoice_number is required")


@router.put("/api/receivables/{receivable_id}")
async def update_receivable(receivable_id: str, request: Request,
                            user=Depends(require_admin)):
    data = await request.json()
    rec = ss.find_record_by_id("receivables", receivable_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Receivable not found")
    ss.update_record("receivables", receivable_id, data)
    _recalculate_bill(data.get("bill_id") or rec.get("bill_id"))
    if rec.get("bill_id") and rec.get("bill_id") != data.get("bill_id"):
        _recalculate_bill(rec.get("bill_id"))
    return {"success": True}


@router.delete("/api/receivables/{receivable_id}")
async def delete_receivable(receivable_id: str, user=Depends(require_admin)):
    rec = ss.find_record_by_id("receivables", receivable_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Receivable not found")
    ss.delete_record("receivables", receivable_id)
    _recalculate_bill(rec.get("bill_id"))
    ss.add_audit_log("Delete", "Billing", receivable_id, "Deleted receivable", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ internals

def _calculate(data):
    """Fill SubTotal, SGST, CGST, TDS and TotalAmount from the amount fields."""
    fixed = ss.to_float(data.get("fixed_amount"))
    variable = ss.to_float(data.get("variable_amount"))
    challan = ss.to_float(data.get("traffic_challan"))
    tolls = ss.to_float(data.get("tollgates"))
    sub_total = fixed + variable
    sgst = data.get("sgst")
    cgst = data.get("cgst")
    tds = data.get("tds")
    sgst = ss.to_float(sgst) if str(sgst or "").strip() else round(sub_total * SGST_RATE, 2)
    cgst = ss.to_float(cgst) if str(cgst or "").strip() else round(sub_total * CGST_RATE, 2)
    tds = ss.to_float(tds) if str(tds or "").strip() else round(sub_total * TDS_RATE, 2)
    total = round(sub_total + sgst + cgst - tds + challan + tolls, 2)
    paid = ss.to_float(data.get("paid_amount"))
    data.update({
        "sub_total": sub_total, "sgst": sgst, "cgst": cgst, "tds": tds,
        "total_amount": total,
        "paid_amount": paid,
        "balance_amount": round(total - paid, 2),
        "payment_status": ("Paid" if paid >= total and total > 0
                           else "Partial" if paid > 0 else "Pending"),
    })
    return data


def _next_invoice_number():
    stamp = datetime.now().strftime("%Y%m")
    existing = [b.get("invoice_number") or "" for b in ss.get_all_records("billing")]
    seq = 1
    while f"INV-{stamp}-{seq:03d}" in existing:
        seq += 1
    return f"INV-{stamp}-{seq:03d}"


def _recalculate_bill(bill_id):
    """Recompute a bill's paid amount, balance and status from its receivables."""
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
        "payment_status": ("Paid" if paid >= total and total > 0
                           else "Partial" if paid > 0 else "Pending"),
    })


def _split_invoice_payment(invoice_number, amount, data, user):
    """Split an invoice-level payment across that invoice's bills, oldest first."""
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
        rid = ss.add_record("receivables", {**data, "bill_id": bill["bill_id"],
                                            "amount": round(portion, 2)})
        _recalculate_bill(bill["bill_id"])
        created.append(rid)
        remaining -= portion
    ss.add_audit_log("Payment added", "Billing", invoice_number,
                     f"₹{amount} split across {len(created)} bill(s)", user["email"])
    return {"success": True, "ids": created,
            "unallocated": round(max(0, remaining), 2)}
