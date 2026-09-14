"""EMI module: vehicle loan EMIs (from Vehicles) plus other EMIs. Mithi is separate."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils import duplicate_check
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["emi"])


@router.get("/emi")
async def emi_page(request: Request, user=Depends(require_view)):
    return render(request, "emi.html")


@router.get("/api/emi")
async def list_emis(user=Depends(require_view)):
    vehicle_emis = []
    for v in ss.get_all_records("vehicles"):
        if (v.get("loan_available") or "").lower() not in ("yes", "true"):
            continue
        vehicle_emis.append({
            "source": "vehicle",
            "vehicle_id": v.get("vehicle_id"),
            "emi_name": f"{v.get('vehicle_number')} Vehicle Loan",
            "vehicle_number": v.get("vehicle_number"),
            "lender_name": v.get("bank_name"),
            "loan_account_number": v.get("loan_account_number"),
            "emi_amount": ss.to_float(v.get("emi_amount")),
            "emi_date": v.get("emi_date"),
            "start_date": v.get("loan_start_date"),
            "end_date": v.get("loan_end_date"),
            "status": _loan_status(v.get("loan_end_date")),
            "next_due": _next_due(v.get("emi_date")),
        })
    other_emis = []
    for e in ss.get_all_records("otheremis"):
        row = dict(e)
        row["source"] = "other"
        row["emi_amount"] = ss.to_float(e.get("emi_amount"))
        row["next_due"] = _next_due(e.get("emi_date"))
        other_emis.append(row)
    other_emis.sort(key=lambda e: e.get("created_date") or "", reverse=True)

    active = [e for e in vehicle_emis + other_emis if (e.get("status") or "Active") == "Active"]
    month = ss.this_month()
    emi_paid = sum(ss.to_float(x.get("amount")) for x in ss.get_all_records("expenses")
                   if x.get("category") == "EMI" and (x.get("expense_date") or "").startswith(month))
    return {
        "vehicle_emis": vehicle_emis,
        "other_emis": other_emis,
        "monthly_total": round(sum(e["emi_amount"] for e in active), 2),
        "active_count": len(active),
        "completed_count": len(vehicle_emis + other_emis) - len(active),
        "emi_paid_this_month": round(emi_paid, 2),
    }


@router.post("/api/emi")
async def add_other_emi(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not (data.get("emi_name") or "").strip():
        raise HTTPException(status_code=400, detail="EMI name is required")
    if duplicate_check.check("otheremis", data):
        raise HTTPException(status_code=400, detail="An EMI with this name and lender already exists")
    data.setdefault("status", "Active")
    data.setdefault("paid_installments", "0")
    emi_id = ss.add_record("otheremis", data)
    ss.add_audit_log("Create", "EMI", emi_id, f"Added EMI {data.get('emi_name')}", user["email"])
    return {"success": True, "id": emi_id}


@router.put("/api/emi/{emi_id}")
async def update_other_emi(emi_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not ss.find_record_by_id("otheremis", emi_id):
        raise HTTPException(status_code=404, detail="EMI not found")
    ss.update_record("otheremis", emi_id, data)
    ss.add_audit_log("Update", "EMI", emi_id, "Updated EMI", user["email"])
    return {"success": True}


@router.delete("/api/emi/{emi_id}")
async def delete_other_emi(emi_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("otheremis", emi_id):
        raise HTTPException(status_code=404, detail="EMI not found")
    ss.delete_record("otheremis", emi_id)
    ss.add_audit_log("Delete", "EMI", emi_id, "Deleted EMI", user["email"])
    return {"success": True}


@router.put("/api/emi/vehicle/{vehicle_id}")
async def update_vehicle_emi(vehicle_id: str, request: Request, user=Depends(require_editor)):
    """Update or remove the loan/EMI details stored on a vehicle."""
    data = await request.json()
    if not ss.find_record_by_id("vehicles", vehicle_id):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if data.get("remove"):
        updates = {"loan_available": "No", "bank_name": "", "loan_account_number": "",
                   "emi_amount": "", "emi_date": "", "loan_start_date": "", "loan_end_date": ""}
        action = "Removed vehicle EMI"
    else:
        allowed = ("loan_available", "bank_name", "loan_account_number", "emi_amount",
                   "emi_date", "loan_start_date", "loan_end_date")
        updates = {k: v for k, v in data.items() if k in allowed}
        updates.setdefault("loan_available", "Yes")
        action = "Updated vehicle EMI"
    ss.update_record("vehicles", vehicle_id, updates)
    ss.add_audit_log("Update", "EMI", vehicle_id, action, user["email"])
    return {"success": True}


def _next_due(emi_day):
    try:
        day = min(max(int(str(emi_day).strip()), 1), 28)
    except (ValueError, TypeError):
        return ""
    now = datetime.now()
    if now.day <= day:
        return now.replace(day=day).strftime("%Y-%m-%d")
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, day).strftime("%Y-%m-%d")


def _loan_status(end_date):
    end = str(end_date or "").strip()
    if end and end[:10] < ss.today_str():
        return "Completed"
    return "Active"
