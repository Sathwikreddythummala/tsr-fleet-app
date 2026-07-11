"""GST module: purchase GST entries plus sales GST read from Billing."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_admin, require_admin_or_viewer
from utils.templates import render

router = APIRouter(tags=["gst"])

GST_RATE = 0.06  # SGST and CGST each, when not provided explicitly


@router.get("/gst")
async def gst_page(request: Request, user=Depends(require_admin_or_viewer)):
    return render(request, "gst.html")


@router.get("/api/gst/purchases")
async def list_purchases(month: str = "", user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("gstpurchases")
    if month:
        rows = [p for p in rows if (p.get("invoice_date") or "").startswith(month)]
    return sorted(rows, key=lambda p: p.get("invoice_date") or "", reverse=True)


@router.post("/api/gst/purchases")
async def add_purchase(request: Request, user=Depends(require_admin)):
    data = _calculate(await request.json())
    if ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    purchase_id = ss.add_record("gstpurchases", data)
    ss.add_audit_log("Create", "GST", purchase_id,
                     f"Purchase {data.get('invoice_number')} ₹{data.get('total_amount')}",
                     user["email"])
    return {"success": True, "id": purchase_id}


@router.put("/api/gst/purchases/{purchase_id}")
async def update_purchase(purchase_id: str, request: Request, user=Depends(require_admin)):
    if not ss.find_record_by_id("gstpurchases", purchase_id):
        raise HTTPException(status_code=404, detail="Purchase not found")
    ss.update_record("gstpurchases", purchase_id, _calculate(await request.json()))
    ss.add_audit_log("Update", "GST", purchase_id, "Updated GST purchase", user["email"])
    return {"success": True}


@router.delete("/api/gst/purchases/{purchase_id}")
async def delete_purchase(purchase_id: str, user=Depends(require_admin)):
    if not ss.find_record_by_id("gstpurchases", purchase_id):
        raise HTTPException(status_code=404, detail="Purchase not found")
    ss.delete_record("gstpurchases", purchase_id)
    ss.add_audit_log("Delete", "GST", purchase_id, "Deleted GST purchase", user["email"])
    return {"success": True}


@router.get("/api/gst/sales")
async def list_sales(month: str = "", user=Depends(require_admin_or_viewer)):
    """Sales GST comes from Billing, grouped by invoice number."""
    groups = {}
    for b in ss.get_all_records("billing"):
        if month and b.get("payment_month") != month:
            continue
        inv = b.get("invoice_number") or "NO-INVOICE"
        g = groups.setdefault(inv, {
            "invoice_number": inv, "invoice_date": b.get("invoice_date"),
            "payment_month": b.get("payment_month"),
            "taxable": 0, "sgst": 0, "cgst": 0, "total": 0, "bill_count": 0,
        })
        g["taxable"] += ss.to_float(b.get("sub_total"))
        g["sgst"] += ss.to_float(b.get("sgst"))
        g["cgst"] += ss.to_float(b.get("cgst"))
        g["total"] += ss.to_float(b.get("total_amount"))
        g["bill_count"] += 1
    rows = sorted(groups.values(), key=lambda g: g.get("invoice_date") or "", reverse=True)
    for g in rows:
        for k in ("taxable", "sgst", "cgst", "total"):
            g[k] = round(g[k], 2)
    return rows


@router.get("/api/gst/summary")
async def gst_summary(month: str = "", user=Depends(require_admin_or_viewer)):
    month = month or ss.this_month()
    purchases = [p for p in ss.get_all_records("gstpurchases")
                 if (p.get("invoice_date") or "").startswith(month)]
    bills = [b for b in ss.get_all_records("billing") if b.get("payment_month") == month]
    input_gst = sum(ss.to_float(p.get("sgst")) + ss.to_float(p.get("cgst")) for p in purchases)
    output_gst = sum(ss.to_float(b.get("sgst")) + ss.to_float(b.get("cgst")) for b in bills)
    return {
        "month": month,
        "purchase_taxable": round(sum(ss.to_float(p.get("amount")) for p in purchases), 2),
        "input_gst": round(input_gst, 2),
        "sales_taxable": round(sum(ss.to_float(b.get("sub_total")) for b in bills), 2),
        "output_gst": round(output_gst, 2),
        "gst_payable": round(output_gst - input_gst, 2),
    }


def _calculate(data):
    amount = ss.to_float(data.get("amount"))
    sgst = data.get("sgst")
    cgst = data.get("cgst")
    sgst = ss.to_float(sgst) if str(sgst or "").strip() else round(amount * GST_RATE, 2)
    cgst = ss.to_float(cgst) if str(cgst or "").strip() else round(amount * GST_RATE, 2)
    data.update({"amount": amount, "sgst": sgst, "cgst": cgst,
                 "total_amount": round(amount + sgst + cgst, 2)})
    return data
