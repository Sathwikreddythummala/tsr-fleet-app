"""Outside Vehicles: hired third-party vehicles and their transactions.

Kept entirely out of owned-vehicle P&L.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.filters import filter_multi, filter_range
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["outside"])

TYPES = ("Hire Charge", "Payment", "Advance", "Other")


@router.get("/outside")
async def outside_page(request: Request, user=Depends(require_view)):
    return render(request, "outside.html")


@router.get("/api/outside/vehicles")
async def list_outside_vehicles(status: str = "", user=Depends(require_view)):
    rows = filter_multi(ss.get_all_records("outsidevehicles"), "status", status,
                        default="Active")
    rows.sort(key=lambda v: (v.get("vehicle_number") or "").upper())
    transactions = ss.get_all_records("outsidetransactions")
    for row in rows:
        own = [t for t in transactions if t.get("vehicle_number") == row.get("vehicle_number")]
        charges = sum(ss.to_float(t.get("amount")) for t in own
                      if t.get("type") in ("Hire Charge", "Other"))
        paid = sum(ss.to_float(t.get("amount")) for t in own
                   if t.get("type") in ("Payment", "Advance"))
        row["total_charges"] = round(charges, 2)
        row["total_paid"] = round(paid, 2)
        row["balance"] = round(charges - paid, 2)
    return {"rows": rows, "total": len(rows)}


@router.post("/api/outside/vehicles")
async def add_outside_vehicle(request: Request, user=Depends(require_editor)):
    data = await request.json()
    number = (data.get("vehicle_number") or "").strip().upper()
    if not number:
        raise HTTPException(status_code=400, detail="Vehicle number is required")
    if any((v.get("vehicle_number") or "").upper() == number
           for v in ss.get_all_records("outsidevehicles")):
        raise HTTPException(status_code=400, detail=f"{number} already exists")
    data["vehicle_number"] = number
    data.setdefault("status", "Active")
    ov_id = ss.add_record("outsidevehicles", data)
    ss.add_audit_log("Create", "Outside", ov_id, f"Added outside vehicle {number}",
                     user["email"])
    return {"success": True, "id": ov_id}


@router.put("/api/outside/vehicles/{ov_id}")
async def update_outside_vehicle(ov_id: str, request: Request, user=Depends(require_editor)):
    if not ss.find_record_by_id("outsidevehicles", ov_id):
        raise HTTPException(status_code=404, detail="Outside vehicle not found")
    data = await request.json()
    if data.get("vehicle_number"):
        data["vehicle_number"] = data["vehicle_number"].strip().upper()
    ss.update_record("outsidevehicles", ov_id, data)
    ss.add_audit_log("Update", "Outside", ov_id, "Updated outside vehicle", user["email"])
    return {"success": True}


@router.delete("/api/outside/vehicles/{ov_id}")
async def delete_outside_vehicle(ov_id: str, user=Depends(require_editor)):
    vehicle = ss.find_record_by_id("outsidevehicles", ov_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Outside vehicle not found")
    for t in ss.get_all_records("outsidetransactions"):
        if t.get("vehicle_number") == vehicle.get("vehicle_number"):
            ss.delete_record("outsidetransactions", t["trans_id"])
    ss.delete_record("outsidevehicles", ov_id)
    ss.add_audit_log("Delete", "Outside", ov_id,
                     f"Deleted {vehicle.get('vehicle_number')} and its transactions",
                     user["email"])
    return {"success": True}


@router.get("/api/outside/transactions")
async def list_transactions(vehicle: str = "", month: str = "", date_from: str = "",
                            date_to: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("outsidetransactions")
    rows = filter_multi(rows, "vehicle_number", vehicle)
    months = [m.strip() for m in (month or "").split(",") if m.strip()]
    if months:
        rows = [t for t in rows
                if (t.get("for_month") or "") in months
                or any((t.get("date") or "").startswith(m) for m in months)]
    rows = filter_range(rows, "date", date_from, date_to)
    rows.sort(key=lambda t: t.get("date") or "", reverse=True)
    charges = sum(ss.to_float(t.get("amount")) for t in rows
                  if t.get("type") in ("Hire Charge", "Other"))
    paid = sum(ss.to_float(t.get("amount")) for t in rows
               if t.get("type") in ("Payment", "Advance"))
    return {"rows": rows, "total_charges": round(charges, 2),
            "total_paid": round(paid, 2), "balance": round(charges - paid, 2)}


@router.post("/api/outside/transactions")
async def add_transaction(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    if data.get("type") not in TYPES:
        raise HTTPException(status_code=400, detail="Invalid transaction type")
    number = (data.get("vehicle_number") or "").strip().upper()
    vehicle = next((v for v in ss.get_all_records("outsidevehicles")
                    if (v.get("vehicle_number") or "").upper() == number), None)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Outside vehicle not found")
    data["vehicle_number"] = number
    data["owner_name"] = vehicle.get("owner_name", "")
    data.setdefault("date", ss.today_str())
    data.setdefault("for_month", (data.get("date") or "")[:7] or ss.this_month())
    trans_id = ss.add_record("outsidetransactions", data)
    ss.add_audit_log("Create", "Outside", trans_id,
                     f"{data['type']} ₹{data.get('amount')} for {number}", user["email"])
    return {"success": True, "id": trans_id}


@router.delete("/api/outside/transactions/{trans_id}")
async def delete_transaction(trans_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("outsidetransactions", trans_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    ss.delete_record("outsidetransactions", trans_id)
    ss.add_audit_log("Delete", "Outside", trans_id, "Deleted transaction", user["email"])
    return {"success": True}


@router.get("/api/outside/summary")
async def outside_summary(user=Depends(require_view)):
    vehicles = ss.get_all_records("outsidevehicles")
    transactions = ss.get_all_records("outsidetransactions")
    charges = sum(ss.to_float(t.get("amount")) for t in transactions
                  if t.get("type") in ("Hire Charge", "Other"))
    paid = sum(ss.to_float(t.get("amount")) for t in transactions
               if t.get("type") in ("Payment", "Advance"))
    month = ss.this_month()
    return {
        "active_vehicles": sum(1 for v in vehicles
                               if (v.get("status") or "Active") == "Active"),
        "total_charges": round(charges, 2),
        "total_paid": round(paid, 2),
        "balance": round(charges - paid, 2),
        "month_charges": round(sum(ss.to_float(t.get("amount")) for t in transactions
                                   if t.get("type") in ("Hire Charge", "Other")
                                   and (t.get("date") or "").startswith(month)), 2),
    }
