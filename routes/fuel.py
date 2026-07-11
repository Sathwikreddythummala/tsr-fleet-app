"""Fuel module: diesel/petrol entries, filters, stats, duplicate check."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils import duplicate_check
from utils.permissions import require_admin, require_admin_or_viewer
from utils.templates import render

router = APIRouter(tags=["fuel"])


@router.get("/fuel")
async def fuel_page(request: Request, user=Depends(require_admin_or_viewer)):
    return render(request, "fuel.html")


@router.get("/api/fuel")
async def list_fuel(date_from: str = "", date_to: str = "", vehicle: str = "",
                    driver: str = "", fuel_type: str = "",
                    user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("fuelentries")
    if date_from:
        rows = [f for f in rows if (f.get("entry_date") or "") >= date_from]
    if date_to:
        rows = [f for f in rows if (f.get("entry_date") or "") <= date_to]
    if vehicle:
        rows = [f for f in rows if f.get("vehicle_number") == vehicle]
    if driver:
        rows = [f for f in rows if f.get("driver_name") == driver]
    if fuel_type:
        rows = [f for f in rows if f.get("fuel_type") == fuel_type]
    rows = sorted(rows, key=lambda f: f.get("entry_date") or "", reverse=True)
    return {
        "rows": rows,
        "total_litres": sum(ss.to_float(f.get("litres")) for f in rows),
        "total_amount": sum(ss.to_float(f.get("amount")) for f in rows),
    }


@router.post("/api/fuel")
async def add_fuel(request: Request, user=Depends(require_admin)):
    data = await request.json()
    if ss.to_float(data.get("amount")) <= 0 and ss.to_float(data.get("litres")) <= 0:
        raise HTTPException(status_code=400, detail="Litres or amount is required")
    if duplicate_check.check("fuelentries", data):
        raise HTTPException(status_code=400, detail="Duplicate fuel entry detected")
    data.setdefault("entry_date", ss.today_str())
    data.setdefault("fuel_type", "Diesel")
    fuel_id = ss.add_record("fuelentries", data)
    ss.add_audit_log("Create", "Fuel", fuel_id,
                     f"{data.get('vehicle_number')} {data.get('litres')}L ₹{data.get('amount')}",
                     user["email"])
    return {"success": True, "id": fuel_id}


@router.put("/api/fuel/{fuel_id}")
async def update_fuel(fuel_id: str, request: Request, user=Depends(require_admin)):
    data = await request.json()
    if not ss.find_record_by_id("fuelentries", fuel_id):
        raise HTTPException(status_code=404, detail="Fuel entry not found")
    if duplicate_check.check("fuelentries", data, exclude_id=fuel_id):
        raise HTTPException(status_code=400, detail="Duplicate fuel entry detected")
    ss.update_record("fuelentries", fuel_id, data)
    ss.add_audit_log("Update", "Fuel", fuel_id, "Updated fuel entry", user["email"])
    return {"success": True}


@router.delete("/api/fuel/{fuel_id}")
async def delete_fuel(fuel_id: str, user=Depends(require_admin)):
    if not ss.find_record_by_id("fuelentries", fuel_id):
        raise HTTPException(status_code=404, detail="Fuel entry not found")
    ss.delete_record("fuelentries", fuel_id)
    ss.add_audit_log("Delete", "Fuel", fuel_id, "Deleted fuel entry", user["email"])
    return {"success": True}


@router.get("/api/fuel/stats")
async def fuel_stats(user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("fuelentries")
    monthly, by_vehicle, by_driver = {}, {}, {}
    for f in rows:
        month = (f.get("entry_date") or "")[:7]
        amount = ss.to_float(f.get("amount"))
        litres = ss.to_float(f.get("litres"))
        if month:
            monthly.setdefault(month, {"amount": 0, "litres": 0})
            monthly[month]["amount"] += amount
            monthly[month]["litres"] += litres
        vehicle = f.get("vehicle_number") or "Unknown"
        by_vehicle[vehicle] = by_vehicle.get(vehicle, 0) + amount
        driver = f.get("driver_name") or "Unknown"
        by_driver[driver] = by_driver.get(driver, 0) + amount
    months = sorted(monthly)[-6:]
    return {
        "months": months,
        "monthly_amount": [round(monthly[m]["amount"], 2) for m in months],
        "monthly_litres": [round(monthly[m]["litres"], 2) for m in months],
        "by_vehicle": dict(sorted(by_vehicle.items(), key=lambda x: -x[1])[:10]),
        "by_driver": dict(sorted(by_driver.items(), key=lambda x: -x[1])[:10]),
    }
