"""Purse module: cash tracking for holders TSR and TRR."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["purse"])

HOLDERS = ("TSR", "TRR")


@router.get("/purse")
async def purse_page(request: Request, user=Depends(require_view)):
    return render(request, "purse.html")


@router.get("/api/purse")
async def list_purse(holder: str = "", month: str = "", date_from: str = "",
                     date_to: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("purse")
    holders = [h.strip() for h in (holder or "").split(",") if h.strip()]
    if holders:
        rows = [p for p in rows if p.get("holder") in holders]
    months = [m.strip() for m in (month or "").split(",") if m.strip()]
    if months:
        rows = [p for p in rows
                if any((p.get("date") or "").startswith(m) for m in months)]
    if date_from:
        rows = [p for p in rows if (p.get("date") or "") >= date_from]
    if date_to:
        rows = [p for p in rows if (p.get("date") or "") <= date_to]
    rows = sorted(rows, key=lambda p: (p.get("date") or "", p.get("created_date") or ""),
                  reverse=True)
    return {"rows": rows, "summary": _summary()}


@router.post("/api/purse")
async def add_purse(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if data.get("holder") not in HOLDERS:
        raise HTTPException(status_code=400, detail="Holder must be TSR or TRR")
    if data.get("type") not in ("Credit", "Debit", "Expense"):
        raise HTTPException(status_code=400, detail="Type must be Credit, Debit or Expense")
    if ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    data.setdefault("date", ss.today_str())
    purse_id = ss.add_record("purse", data)
    ss.add_audit_log("Create", "Purse", purse_id,
                     f"{data['holder']} {data['type']} ₹{data.get('amount')}", user["email"])
    return {"success": True, "id": purse_id}


@router.delete("/api/purse/{purse_id}")
async def delete_purse(purse_id: str, user=Depends(require_editor)):
    entry = ss.find_record_by_id("purse", purse_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Purse entry not found")
    if entry.get("reference_id"):
        raise HTTPException(status_code=400,
                            detail="This entry is linked to an expense - delete the expense instead")
    ss.delete_record("purse", purse_id)
    ss.add_audit_log("Delete", "Purse", purse_id, "Deleted purse entry", user["email"])
    return {"success": True}


def _summary():
    purse = ss.get_all_records("purse")
    expenses = ss.get_all_records("expenses")
    linked = {p.get("reference_id") for p in purse if p.get("reference_id")}
    summary = {}
    for holder in HOLDERS:
        entries = [p for p in purse if p.get("holder") == holder]
        given = sum(ss.to_float(p.get("amount")) for p in entries if p.get("type") == "Credit")
        spent = sum(ss.to_float(p.get("amount")) for p in entries
                    if p.get("type") in ("Debit", "Expense"))
        # holder-paid expenses that were not already mirrored into the purse
        spent += sum(ss.to_float(e.get("amount")) for e in expenses
                     if e.get("paid_by") == holder and e.get("expense_id") not in linked)
        summary[holder] = {"given": round(given, 2), "spent": round(spent, 2),
                           "balance": round(given - spent, 2)}
    summary["total_balance"] = round(sum(s["balance"] for s in summary.values()), 2)
    return summary
