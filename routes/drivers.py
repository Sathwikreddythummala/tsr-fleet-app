"""Employees/Drivers: master data, documents, PINs and the salary engine.

Salary model (per month):
  - every vehicle carries a MonthlySalary; per-day rate = salary / days in month
  - for each day the driver of a vehicle is resolved as
        single-day override  >  date-range assignment  >  vehicle default driver
    (a driver who overrides on another vehicle that day is not double counted)
  - each earned day is weighted by attendance: Present 1.0, Half Day 0.5, Absent 0
  - Gross   = sum(effective days x per-day rate) across vehicles
  - Net     = Gross + Incentive - Salary paid - Advance - Meals - Deductions - Other
    (deductions and incentives are counted once per driver, not per vehicle)

Salary components are ordinary Expenses with category 'Driver Expense' and
sub category Salary / Advance / Meals / Deductions / Other.
"""
import random
from calendar import monthrange

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from services import sheets_service as ss
from utils.exports import excel_response
from utils.filters import filter_multi, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["drivers"])

DRIVER_CATEGORY = "Driver Expense"
COMPONENTS = ("Salary", "Advance", "Meals", "Deductions", "Other")
ATTENDANCE_WEIGHTS = {"Present": 1.0, "Half Day": 0.5, "Absent": 0.0}


@router.get("/drivers")
async def drivers_page(request: Request, user=Depends(require_view)):
    return render(request, "drivers.html")


@router.get("/drivers/details/{driver_id}")
async def driver_details_page(request: Request, driver_id: str,
                              user=Depends(require_view)):
    driver = ss.find_record_by_id("drivers", driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    return render(request, "driver_details.html", {"driver": driver})


@router.get("/salaries")
async def salaries_page(request: Request, user=Depends(require_view)):
    return render(request, "salaries.html")


# ------------------------------------------------------------------ master data

@router.get("/api/drivers")
async def list_drivers(employee_type: str = "", status: str = "", search: str = "",
                       user=Depends(require_view)):
    rows = ss.get_all_records("drivers")
    rows = filter_multi(rows, "employee_type", employee_type, default="Driver")
    rows = filter_multi(rows, "status", status, default="Active")
    rows = filter_search(rows, search, ["driver_name", "mobile_number",
                                        "assigned_vehicle", "email"])
    rows.sort(key=lambda d: (d.get("driver_name") or "").lower())
    return rows


@router.get("/api/drivers/{driver_id}")
async def get_driver(driver_id: str, user=Depends(require_view)):
    driver = ss.find_record_by_id("drivers", driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    return driver


@router.post("/api/drivers")
async def add_driver(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not (data.get("driver_name") or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    mobile = _clean_mobile(data.get("mobile_number"))
    if mobile and _driver_by_mobile(mobile):
        raise HTTPException(status_code=400,
                            detail=f"Mobile {mobile} is already used by another employee")
    data.setdefault("status", "Active")
    data.setdefault("employee_type", "Driver")
    driver_id = ss.add_record("drivers", data)
    ss.add_audit_log("Create", "Drivers", driver_id,
                     f"Added {data.get('employee_type')} {data.get('driver_name')}",
                     user["email"])
    return {"success": True, "id": driver_id}


@router.put("/api/drivers/{driver_id}")
async def update_driver(driver_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    driver = ss.find_record_by_id("drivers", driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    mobile = _clean_mobile(data.get("mobile_number"))
    if mobile:
        clash = _driver_by_mobile(mobile)
        if clash and clash.get("driver_id") != driver_id:
            raise HTTPException(status_code=400,
                                detail=f"Mobile {mobile} is already used by another employee")
    data.pop("portal_pin", None)  # PIN only changes through the Driver App page
    ss.update_record("drivers", driver_id, data)
    ss.add_audit_log("Update", "Drivers", driver_id,
                     f"Updated {data.get('driver_name', driver_id)}", user["email"])
    return {"success": True}


@router.delete("/api/drivers/{driver_id}")
async def delete_driver(driver_id: str, user=Depends(require_editor)):
    driver = ss.find_record_by_id("drivers", driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    ss.delete_record("drivers", driver_id)
    ss.add_audit_log("Delete", "Drivers", driver_id,
                     f"Deleted {driver.get('driver_name')}", user["email"])
    return {"success": True}


@router.get("/api/drivers/{driver_id}/documents")
async def driver_documents(driver_id: str, user=Depends(require_view)):
    return [d for d in ss.get_all_records("documents")
            if d.get("entity_type") == "driver" and d.get("entity_id") == driver_id]


@router.post("/api/drivers/{driver_id}/upload")
async def upload_driver_document(driver_id: str, document_type: str = Form(...),
                                 file: UploadFile = File(...),
                                 user=Depends(require_editor)):
    from routes.documents import store_document
    return await store_document("driver", driver_id, document_type, file, user)


# ------------------------------------------------------------------ salary engine

def _resolve_daily_drivers(month, days):
    """Map (vehicle_number, date) -> driver_id for every day of the month."""
    vehicles = ss.get_all_records("vehicles")
    assignments = ss.get_all_records("vehicleassignments")
    drivers_by_name = {(d.get("driver_name") or "").strip().lower(): d
                       for d in ss.get_all_records("drivers")}

    overrides, ranges = {}, []
    for a in assignments:
        start, end = a.get("start_date") or "", a.get("end_date") or ""
        if start and start == end:
            overrides[(a.get("vehicle_number"), start)] = a.get("driver_id")
        elif start:
            ranges.append(a)

    resolved = {}
    for day in range(1, days + 1):
        date = f"{month}-{day:02d}"
        # drivers who are explicitly placed on some vehicle today
        placed_today = {driver_id for (veh, d), driver_id in overrides.items()
                        if d == date and driver_id}
        for vehicle in vehicles:
            number = vehicle.get("vehicle_number")
            driver_id = overrides.get((number, date))
            if not driver_id:
                match = next((a for a in ranges
                              if a.get("vehicle_number") == number
                              and (a.get("start_date") or "") <= date
                              and (not a.get("end_date") or a["end_date"] >= date)), None)
                driver_id = (match or {}).get("driver_id")
            if not driver_id:
                default = drivers_by_name.get(
                    (vehicle.get("default_driver") or "").strip().lower())
                candidate = (default or {}).get("driver_id")
                # a default driver who is overriding elsewhere today isn't counted here
                if candidate and candidate not in placed_today:
                    driver_id = candidate
            if driver_id:
                resolved[(number, date)] = driver_id
    return resolved


def _salary_engine(month):
    """Compute gross/net salary per employee for a month."""
    month = month or ss.this_month()
    year, mon = (int(x) for x in month.split("-")[:2])
    days = monthrange(year, mon)[1]

    vehicles = {v.get("vehicle_number"): v for v in ss.get_all_records("vehicles")}
    drivers = {d.get("driver_id"): d for d in ss.get_all_records("drivers")}
    resolved = _resolve_daily_drivers(month, days)

    attendance = {}
    for a in ss.get_all_records("attendance"):
        if (a.get("date") or "").startswith(month):
            attendance[(a.get("driver_id"), a.get("date"))] = a.get("status")

    # accrue per-vehicle earnings
    accrual = {}      # driver_id -> {"gross": x, "days": y, "vehicles": {num: {...}}}
    vehicle_totals = {}
    for (number, date), driver_id in resolved.items():
        vehicle = vehicles.get(number)
        if not vehicle or (vehicle.get("vehicle_status") or "Active") != "Active":
            continue
        per_day = ss.to_float(vehicle.get("monthly_salary")) / days
        if per_day <= 0:
            continue
        status = attendance.get((driver_id, date))
        weight = ATTENDANCE_WEIGHTS.get(status, 1.0) if status else 1.0
        if weight <= 0:
            continue
        entry = accrual.setdefault(driver_id, {"gross": 0.0, "days": 0.0, "vehicles": {}})
        entry["gross"] += per_day * weight
        entry["days"] += weight
        per_vehicle = entry["vehicles"].setdefault(number, {"days": 0.0, "amount": 0.0})
        per_vehicle["days"] += weight
        per_vehicle["amount"] += per_day * weight
        total = vehicle_totals.setdefault(number, {"days": 0.0, "amount": 0.0,
                                                   "drivers": set()})
        total["days"] += weight
        total["amount"] += per_day * weight
        total["drivers"].add(driver_id)

    # per-driver deductions and incentives (counted once, not per vehicle)
    paid = {}
    for e in ss.get_all_records("expenses"):
        if e.get("category") != DRIVER_CATEGORY or e.get("for_month") != month:
            continue
        name = (e.get("driver_name") or "").strip().lower()
        if not name:
            continue
        bucket = paid.setdefault(name, {c: 0.0 for c in COMPONENTS})
        component = e.get("sub_category") if e.get("sub_category") in COMPONENTS else "Other"
        bucket[component] += ss.to_float(e.get("amount"))
    incentives = {}
    for i in ss.get_all_records("incentives"):
        if i.get("for_month") == month:
            incentives[i.get("driver_id")] = (incentives.get(i.get("driver_id"), 0.0)
                                              + ss.to_float(i.get("amount")))

    rows = []
    for driver_id, driver in drivers.items():
        if (driver.get("status") or "Active") != "Active" and driver_id not in accrual:
            continue
        entry = accrual.get(driver_id, {"gross": 0.0, "days": 0.0, "vehicles": {}})
        name_key = (driver.get("driver_name") or "").strip().lower()
        components = paid.get(name_key, {c: 0.0 for c in COMPONENTS})
        gross = entry["gross"]
        # employees with no vehicle accrual fall back to their profile salary
        if gross <= 0 and not entry["vehicles"]:
            gross = ss.to_float(driver.get("salary"))
        incentive = incentives.get(driver_id, 0.0)
        deductions = sum(components[c] for c in ("Salary", "Advance", "Meals",
                                                 "Deductions", "Other"))
        rows.append({
            "driver_id": driver_id,
            "driver_name": driver.get("driver_name"),
            "employee_type": driver.get("employee_type") or "Driver",
            "status": driver.get("status") or "Active",
            "effective_days": round(entry["days"], 2),
            "gross": round(gross, 2),
            "incentive": round(incentive, 2),
            "salary_paid": round(components["Salary"], 2),
            "advance": round(components["Advance"], 2),
            "meals": round(components["Meals"], 2),
            "deduction": round(components["Deductions"], 2),
            "other": round(components["Other"], 2),
            "net_payable": round(gross + incentive - deductions, 2),
            "vehicles": {k: {"days": round(v["days"], 2),
                             "amount": round(v["amount"], 2)}
                         for k, v in entry["vehicles"].items()},
        })
    rows.sort(key=lambda r: (r["driver_name"] or "").lower())

    vehicle_rows = [{
        "vehicle_number": number,
        "monthly_salary": ss.to_float(vehicles[number].get("monthly_salary")),
        "days": round(v["days"], 2),
        "amount": round(v["amount"], 2),
        "driver_count": len(v["drivers"]),
    } for number, v in sorted(vehicle_totals.items())]

    return {
        "month": month,
        "month_name": ss.month_name(month),
        "days_in_month": days,
        "rows": rows,
        "vehicles": vehicle_rows,
        "total_gross": round(sum(r["gross"] for r in rows), 2),
        "total_net": round(sum(r["net_payable"] for r in rows), 2),
        "total_advance": round(sum(r["advance"] for r in rows), 2),
        "total_incentive": round(sum(r["incentive"] for r in rows), 2),
    }


@router.get("/api/salaries")
async def salaries(month: str = "", user=Depends(require_view)):
    return _salary_engine(month)


@router.get("/api/vehicle-salaries")
async def vehicle_salaries(month: str = "", user=Depends(require_view)):
    data = _salary_engine(month)
    return {"month": data["month"], "month_name": data["month_name"],
            "rows": data["vehicles"],
            "total": round(sum(v["amount"] for v in data["vehicles"]), 2)}


@router.get("/api/drivers/{driver_id}/salary")
async def driver_salary(driver_id: str, month: str = "", user=Depends(require_view)):
    data = _salary_engine(month)
    row = next((r for r in data["rows"] if r["driver_id"] == driver_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Employee not found")
    entries = [e for e in ss.get_all_records("expenses")
               if e.get("category") == DRIVER_CATEGORY
               and e.get("for_month") == data["month"]
               and (e.get("driver_name") or "").strip().lower()
               == (row["driver_name"] or "").strip().lower()]
    entries.sort(key=lambda e: e.get("expense_date") or "", reverse=True)
    incentives = [i for i in ss.get_all_records("incentives")
                  if i.get("driver_id") == driver_id
                  and i.get("for_month") == data["month"]]
    return {"month": data["month"], "month_name": data["month_name"],
            "summary": row, "entries": entries, "incentives": incentives}


@router.post("/api/incentive")
async def add_incentive(request: Request, user=Depends(require_editor)):
    data = await request.json()
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    if ss.to_float(data.get("amount")) == 0:
        raise HTTPException(status_code=400, detail="Amount is required")
    payload = {
        "driver_id": driver["driver_id"], "driver_name": driver.get("driver_name"),
        "for_month": data.get("for_month") or ss.this_month(),
        "amount": ss.to_float(data.get("amount")),
        "description": data.get("description", ""), "entered_by": user["email"],
    }
    existing = next((i for i in ss.get_all_records("incentives")
                     if i.get("driver_id") == driver["driver_id"]
                     and i.get("for_month") == payload["for_month"]), None)
    if existing:
        ss.update_record("incentives", existing["incentive_id"], payload)
        incentive_id = existing["incentive_id"]
    else:
        incentive_id = ss.add_record("incentives", payload)
    ss.add_audit_log("Update", "Salaries", incentive_id,
                     f"Incentive ₹{payload['amount']} for {driver.get('driver_name')}"
                     f" ({payload['for_month']})", user["email"])
    return {"success": True, "id": incentive_id}


@router.delete("/api/incentive/{incentive_id}")
async def delete_incentive(incentive_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("incentives", incentive_id):
        raise HTTPException(status_code=404, detail="Incentive not found")
    ss.delete_record("incentives", incentive_id)
    ss.add_audit_log("Delete", "Salaries", incentive_id, "Deleted incentive",
                     user["email"])
    return {"success": True}


@router.post("/api/salaries/component")
async def add_component(request: Request, user=Depends(require_editor)):
    """Record a salary component (Salary paid / Advance / Meals / Deductions)."""
    data = await request.json()
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    component = data.get("sub_category")
    if component not in COMPONENTS:
        raise HTTPException(status_code=400, detail="Invalid salary component")
    if ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    month = data.get("for_month") or ss.this_month()
    expense_id = ss.add_record("expenses", {
        "expense_date": data.get("expense_date") or ss.today_str(),
        "for_month": month, "expense_for": "Driver Expense",
        "vehicle_number": driver.get("assigned_vehicle", ""),
        "driver_name": driver.get("driver_name"), "category": DRIVER_CATEGORY,
        "sub_category": component, "description": data.get("description", ""),
        "amount": ss.to_float(data.get("amount")),
        "payment_mode": data.get("payment_mode", "Cash"),
        "paid_by": data.get("paid_by", ""),
    })
    ss.add_audit_log("Create", "Salaries", expense_id,
                     f"{component} ₹{data.get('amount')} for {driver.get('driver_name')}",
                     user["email"])
    return {"success": True, "id": expense_id}


@router.get("/api/salaries/export")
async def export_salaries(month: str = "", user=Depends(require_view)):
    data = _salary_engine(month)
    headers = ["Employee", "Type", "Days", "Gross", "Incentive", "Salary Paid",
               "Advance", "Meals", "Deductions", "Other", "Net Payable"]
    rows = [[r["driver_name"], r["employee_type"], r["effective_days"], r["gross"],
             r["incentive"], r["salary_paid"], r["advance"], r["meals"],
             r["deduction"], r["other"], r["net_payable"]] for r in data["rows"]]
    totals = ["TOTAL", "", "", data["total_gross"], data["total_incentive"], "",
              data["total_advance"], "", "", "", data["total_net"]]
    return excel_response(f"Salaries {data['month']}", headers, rows,
                          f"salaries_{data['month']}.xlsx", totals)


# ------------------------------------------------------------------ driver PINs

@router.get("/api/drivers/pins/list")
async def list_pins(user=Depends(require_editor)):
    rows = []
    for d in sorted(ss.get_all_records("drivers"),
                    key=lambda x: (x.get("driver_name") or "").lower()):
        if (d.get("status") or "Active") != "Active":
            continue
        rows.append({
            "driver_id": d.get("driver_id"),
            "driver_name": d.get("driver_name"),
            "employee_type": d.get("employee_type") or "Driver",
            "mobile_number": d.get("mobile_number") or "",
            "assigned_vehicle": d.get("assigned_vehicle") or "",
            "has_pin": bool((d.get("portal_pin") or "").strip()),
        })
    return {"rows": rows, "with_pin": sum(1 for r in rows if r["has_pin"]),
            "total": len(rows)}


@router.post("/api/drivers/pins/set")
async def set_pin(request: Request, user=Depends(require_editor)):
    data = await request.json()
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not _clean_mobile(driver.get("mobile_number")):
        raise HTTPException(status_code=400,
                            detail="Add a 10-digit mobile number for this employee first")
    pin = str(data.get("pin") or "").strip()
    if data.get("generate"):
        pin = f"{random.randint(1000, 9999)}"
    if not (pin.isdigit() and 4 <= len(pin) <= 6):
        raise HTTPException(status_code=400, detail="PIN must be 4 to 6 digits")
    ss.update_record("drivers", driver["driver_id"], {"portal_pin": pin})
    ss.add_audit_log("Update", "DriverApp", driver["driver_id"],
                     f"Set portal PIN for {driver.get('driver_name')}", user["email"])
    return {"success": True, "pin": pin, "driver_name": driver.get("driver_name"),
            "mobile_number": driver.get("mobile_number")}


@router.post("/api/drivers/pins/clear")
async def clear_pin(request: Request, user=Depends(require_editor)):
    data = await request.json()
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    ss.update_record("drivers", driver["driver_id"], {"portal_pin": ""})
    ss.add_audit_log("Update", "DriverApp", driver["driver_id"],
                     f"Cleared portal PIN for {driver.get('driver_name')}", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ helpers

def _clean_mobile(mobile):
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _driver_by_mobile(mobile):
    target = _clean_mobile(mobile)
    if not target:
        return None
    for d in ss.get_all_records("drivers"):
        if _clean_mobile(d.get("mobile_number")) == target:
            return d
    return None
