"""Driver Portal: self-service view of salary, advance, meals and diesel entries."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_driver
from utils.templates import render

router = APIRouter(tags=["driver_portal"])


@router.get("/driver-portal")
async def driver_portal_page(request: Request, user=Depends(require_driver)):
    return render(request, "driver_portal.html")


@router.get("/api/driver-portal/me")
async def my_profile(user=Depends(require_driver)):
    driver = _my_driver(user)
    vehicle = None
    if driver.get("assigned_vehicle"):
        vehicle = next((v for v in ss.get_all_records("vehicles")
                        if v.get("vehicle_number") == driver["assigned_vehicle"]), None)
    return {
        "driver": {k: driver.get(k) for k in
                   ("driver_id", "driver_name", "employee_type", "mobile_number",
                    "email", "salary", "joining_date", "assigned_vehicle", "status")},
        "vehicle": {k: (vehicle or {}).get(k) for k in
                    ("vehicle_number", "vehicle_type", "vehicle_status")} if vehicle else None,
    }


@router.get("/api/driver-portal/entries")
async def my_entries(month: str = "", user=Depends(require_driver)):
    driver = _my_driver(user)
    driver_id = driver.get("driver_id")
    name = driver.get("driver_name")

    adjustments = [a for a in ss.get_all_records("incentives")
                   if a.get("driver_id") == driver_id]
    diesel = [f for f in ss.get_all_records("fuelentries")
              if (f.get("driver_name") or "") == name]
    if month:
        adjustments = [a for a in adjustments if a.get("for_month") == month]
        diesel = [d for d in diesel if (d.get("entry_date") or "").startswith(month)]

    def total(entry_type):
        return round(sum(ss.to_float(a.get("amount")) for a in adjustments
                         if a.get("entry_type") == entry_type), 2)

    return {
        "salary": ss.to_float(driver.get("salary")),
        "advance_entries": [a for a in adjustments if a.get("entry_type") == "Advance"],
        "meals_entries": [a for a in adjustments if a.get("entry_type") == "Meals"],
        "diesel_entries": sorted(diesel, key=lambda d: d.get("entry_date") or "",
                                 reverse=True),
        "total_advance": total("Advance"),
        "total_meals": total("Meals"),
        "total_incentive": total("Incentive"),
        "total_diesel_litres": round(sum(ss.to_float(d.get("litres")) for d in diesel), 2),
        "total_diesel_amount": round(sum(ss.to_float(d.get("amount")) for d in diesel), 2),
    }


@router.post("/api/driver-portal/diesel")
async def add_my_diesel(request: Request, user=Depends(require_driver)):
    """Drivers can add their own diesel entries for their assigned vehicle."""
    driver = _my_driver(user)
    data = await request.json()
    if ss.to_float(data.get("litres")) <= 0 and ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Litres or amount is required")
    entry = {
        "entry_date": data.get("entry_date") or ss.today_str(),
        "vehicle_number": data.get("vehicle_number") or driver.get("assigned_vehicle", ""),
        "driver_name": driver.get("driver_name"),
        "fuel_type": data.get("fuel_type") or "Diesel",
        "litres": data.get("litres"),
        "amount": data.get("amount"),
        "kilometre": data.get("kilometre", ""),
        "fuel_station": data.get("fuel_station", ""),
        "payment_mode": data.get("payment_mode", "Cash"),
    }
    fuel_id = ss.add_record("fuelentries", entry)
    ss.add_audit_log("Create", "DriverPortal", fuel_id,
                     f"Driver diesel entry {entry['vehicle_number']} {entry['litres']}L",
                     user.get("email") or driver.get("driver_id"))
    return {"success": True, "id": fuel_id}


def _my_driver(user):
    driver = ss.find_record_by_id("drivers", user.get("driver_id") or "")
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    return driver
