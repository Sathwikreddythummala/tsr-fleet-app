"""Attendance module: monthly grid feeding the salary engine.

Status values: Present (1.0 day), Half Day (0.5), Absent (0).
"""
from calendar import monthrange

from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["attendance"])

STATUSES = ("Present", "Half Day", "Absent")
WEIGHTS = {"Present": 1.0, "Half Day": 0.5, "Absent": 0.0}


@router.get("/attendance")
async def attendance_page(request: Request, user=Depends(require_view)):
    return render(request, "attendance.html")


@router.get("/api/attendance/list")
async def list_attendance(month: str = "", user=Depends(require_view)):
    """Monthly grid: every active employee with a status per day."""
    month = month or ss.this_month()
    year, mon = (int(x) for x in month.split("-")[:2])
    days = monthrange(year, mon)[1]

    marks = {}
    for row in ss.get_all_records("attendance"):
        if (row.get("date") or "").startswith(month):
            marks[(row.get("driver_id"), row.get("date"))] = row

    rows = []
    for driver in sorted(ss.get_all_records("drivers"),
                         key=lambda d: (d.get("driver_name") or "").lower()):
        if (driver.get("status") or "Active") != "Active":
            continue
        entries, present = {}, 0.0
        for day in range(1, days + 1):
            date = f"{month}-{day:02d}"
            mark = marks.get((driver.get("driver_id"), date))
            status = (mark or {}).get("status") or ""
            if status:
                entries[date] = {"status": status,
                                 "attendance_id": mark.get("attendance_id")}
                present += WEIGHTS.get(status, 0.0)
        rows.append({
            "driver_id": driver.get("driver_id"),
            "driver_name": driver.get("driver_name"),
            "employee_type": driver.get("employee_type") or "Driver",
            "assigned_vehicle": driver.get("assigned_vehicle") or "",
            "entries": entries,
            "effective_days": round(present, 2),
        })
    return {"month": month, "days": days, "rows": rows}


@router.post("/api/attendance/mark")
async def mark_attendance(request: Request, user=Depends(require_editor)):
    """Set (or clear) one employee's status for one date."""
    data = await request.json()
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    date = data.get("date") or ss.today_str()
    status = data.get("status") or ""
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    existing = next((a for a in ss.get_all_records("attendance")
                     if a.get("driver_id") == driver["driver_id"]
                     and a.get("date") == date), None)
    if not status:
        if existing:
            ss.delete_record("attendance", existing["attendance_id"])
        return {"success": True, "status": ""}
    if existing:
        ss.update_record("attendance", existing["attendance_id"],
                         {"status": status, "marked_by": user["email"]})
        attendance_id = existing["attendance_id"]
    else:
        attendance_id = ss.add_record("attendance", {
            "date": date, "driver_id": driver["driver_id"],
            "driver_name": driver.get("driver_name"), "status": status,
            "marked_by": user["email"],
        })
    return {"success": True, "status": status, "attendance_id": attendance_id}


@router.post("/api/attendance/bulk")
async def bulk_mark(request: Request, user=Depends(require_editor)):
    """Mark every active employee Present/Absent/Half Day for one date."""
    data = await request.json()
    date = data.get("date") or ss.today_str()
    status = data.get("status")
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    existing = {a.get("driver_id"): a for a in ss.get_all_records("attendance")
                if a.get("date") == date}
    count = 0
    for driver in ss.get_all_records("drivers"):
        if (driver.get("status") or "Active") != "Active":
            continue
        prior = existing.get(driver["driver_id"])
        if prior:
            ss.update_record("attendance", prior["attendance_id"],
                             {"status": status, "marked_by": user["email"]})
        else:
            ss.add_record("attendance", {
                "date": date, "driver_id": driver["driver_id"],
                "driver_name": driver.get("driver_name"), "status": status,
                "marked_by": user["email"],
            })
        count += 1
    ss.add_audit_log("Update", "Attendance", date,
                     f"Bulk marked {count} employees {status}", user["email"])
    return {"success": True, "count": count}


@router.post("/api/attendance/set-day-driver")
async def set_day_driver(request: Request, user=Depends(require_editor)):
    """Single-day driver override for a vehicle (start == end assignment)."""
    data = await request.json()
    date = data.get("date") or ss.today_str()
    vehicle = ss.find_record_by_id("vehicles", data.get("vehicle_id", ""))
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    driver_id = data.get("driver_id") or ""

    for a in ss.get_all_records("vehicleassignments"):
        if (a.get("vehicle_id") == vehicle["vehicle_id"]
                and a.get("start_date") == date and a.get("end_date") == date):
            ss.delete_record("vehicleassignments", a["assignment_id"])
    if not driver_id:
        return {"success": True, "cleared": True}

    driver = ss.find_record_by_id("drivers", driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    assignment_id = ss.add_record("vehicleassignments", {
        "vehicle_id": vehicle["vehicle_id"],
        "vehicle_number": vehicle.get("vehicle_number"),
        "driver_id": driver_id, "driver_name": driver.get("driver_name"),
        "start_date": date, "end_date": date,
    })
    ss.add_audit_log("Assign", "Attendance", assignment_id,
                     f"{driver.get('driver_name')} drove {vehicle.get('vehicle_number')} on {date}",
                     user["email"])
    return {"success": True, "id": assignment_id}


@router.get("/api/attendance/summary")
async def attendance_summary(month: str = "", user=Depends(require_view)):
    month = month or ss.this_month()
    rows = [a for a in ss.get_all_records("attendance")
            if (a.get("date") or "").startswith(month)]
    counts = {s: sum(1 for a in rows if a.get("status") == s) for s in STATUSES}
    return {"month": month, "marked": len(rows), **counts}
