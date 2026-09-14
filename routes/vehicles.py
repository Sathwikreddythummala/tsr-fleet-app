"""Vehicles: master data, driver assignments, vendor assignment, documents."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from services import sheets_service as ss
from utils.filters import filter_multi, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["vehicles"])


@router.get("/vehicles")
async def vehicles_page(request: Request, user=Depends(require_view)):
    return render(request, "vehicles.html")


@router.get("/vehicles/details/{vehicle_id}")
async def vehicle_details_page(request: Request, vehicle_id: str,
                               user=Depends(require_view)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return render(request, "vehicle_details.html", {"vehicle": vehicle})


@router.get("/api/vehicles")
async def list_vehicles(status: str = "", vehicle_type: str = "", search: str = "",
                        user=Depends(require_view)):
    rows = ss.get_all_records("vehicles")
    rows = filter_multi(rows, "vehicle_status", status, default="Active")
    rows = filter_multi(rows, "vehicle_type", vehicle_type)
    rows = filter_search(rows, search, ["vehicle_number", "default_driver",
                                        "vehicle_type", "default_vendor"])
    rows.sort(key=lambda v: (v.get("vehicle_number") or "").upper())
    return rows


@router.get("/api/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: str, user=Depends(require_view)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.post("/api/vehicles")
async def add_vehicle(request: Request, user=Depends(require_editor)):
    data = await request.json()
    number = (data.get("vehicle_number") or "").strip().upper()
    if not number:
        raise HTTPException(status_code=400, detail="Vehicle number is required")
    if any((v.get("vehicle_number") or "").upper() == number
           for v in ss.get_all_records("vehicles")):
        raise HTTPException(status_code=400, detail=f"Vehicle {number} already exists")
    data["vehicle_number"] = number
    data.setdefault("vehicle_status", "Active")
    vehicle_id = ss.add_record("vehicles", data)
    _sync_driver_assignment(number, data.get("default_driver"))
    ss.add_audit_log("Create", "Vehicles", vehicle_id, f"Added vehicle {number}",
                     user["email"])
    return {"success": True, "id": vehicle_id}


@router.put("/api/vehicles/{vehicle_id}")
async def update_vehicle(vehicle_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if data.get("vehicle_number"):
        data["vehicle_number"] = data["vehicle_number"].strip().upper()
    ss.update_record("vehicles", vehicle_id, data)
    number = data.get("vehicle_number") or vehicle.get("vehicle_number")
    if "default_driver" in data:
        _sync_driver_assignment(number, data.get("default_driver"))
    ss.add_audit_log("Update", "Vehicles", vehicle_id, f"Updated vehicle {number}",
                     user["email"])
    return {"success": True}


@router.delete("/api/vehicles/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, user=Depends(require_editor)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    ss.delete_record("vehicles", vehicle_id)
    for a in ss.get_all_records("vehicleassignments"):
        if a.get("vehicle_id") == vehicle_id:
            ss.delete_record("vehicleassignments", a["assignment_id"])
    ss.add_audit_log("Delete", "Vehicles", vehicle_id,
                     f"Deleted vehicle {vehicle.get('vehicle_number')}", user["email"])
    return {"success": True}


@router.post("/api/vehicles/assign-vendor")
async def assign_vendor(request: Request, user=Depends(require_editor)):
    data = await request.json()
    vehicle = ss.find_record_by_id("vehicles", data.get("vehicle_id", ""))
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    vendor_name = (data.get("vendor_name") or "").strip()
    ss.update_record("vehicles", vehicle["vehicle_id"], {"default_vendor": vendor_name})
    ss.add_audit_log("Assign", "Vehicles", vehicle["vehicle_id"],
                     f"{vehicle.get('vehicle_number')} -> {vendor_name or 'no vendor'}",
                     user["email"])
    return {"success": True}


@router.post("/api/vehicles/sync-drivers")
async def sync_drivers(user=Depends(require_editor)):
    """Re-align drivers.assigned_vehicle with each vehicle's default driver."""
    changed = 0
    for vehicle in ss.get_all_records("vehicles"):
        if _sync_driver_assignment(vehicle.get("vehicle_number"),
                                   vehicle.get("default_driver")):
            changed += 1
    ss.add_audit_log("Update", "Vehicles", "sync",
                     f"Synced driver assignments ({changed} change(s))", user["email"])
    return {"success": True, "changed": changed}


# ------------------------------------------------------------------ assignments

@router.get("/api/vehicles/assignments/list")
async def list_assignments(vehicle_id: str = "", driver_id: str = "", month: str = "",
                           user=Depends(require_view)):
    rows = ss.get_all_records("vehicleassignments")
    if vehicle_id:
        rows = [a for a in rows if a.get("vehicle_id") == vehicle_id]
    if driver_id:
        rows = [a for a in rows if a.get("driver_id") == driver_id]
    if month:
        rows = [a for a in rows
                if (a.get("start_date") or "")[:7] <= month
                and (not a.get("end_date") or a["end_date"][:7] >= month)]
    rows.sort(key=lambda a: a.get("start_date") or "", reverse=True)
    for row in rows:
        row["is_override"] = bool(row.get("start_date")
                                  and row.get("start_date") == row.get("end_date"))
    return {"rows": rows, "total": len(rows)}


@router.post("/api/vehicles/assignments")
async def add_assignment(request: Request, user=Depends(require_editor)):
    data = await request.json()
    vehicle = ss.find_record_by_id("vehicles", data.get("vehicle_id", ""))
    driver = ss.find_record_by_id("drivers", data.get("driver_id", ""))
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if not driver:
        raise HTTPException(status_code=404, detail="Employee not found")
    start = data.get("start_date") or ss.today_str()
    end = data.get("end_date") or ""
    if end and end < start:
        raise HTTPException(status_code=400, detail="End date is before the start date")

    clash = next((a for a in ss.get_all_records("vehicleassignments")
                  if a.get("vehicle_id") == vehicle["vehicle_id"]
                  and _overlaps(start, end, a.get("start_date"), a.get("end_date"))), None)
    if clash:
        raise HTTPException(
            status_code=400,
            detail=f"{vehicle.get('vehicle_number')} already has "
                   f"{clash.get('driver_name')} assigned in that period")

    assignment_id = ss.add_record("vehicleassignments", {
        "vehicle_id": vehicle["vehicle_id"],
        "vehicle_number": vehicle.get("vehicle_number"),
        "driver_id": driver["driver_id"], "driver_name": driver.get("driver_name"),
        "start_date": start, "end_date": end,
    })
    ss.add_audit_log("Assign", "Vehicles", assignment_id,
                     f"{driver.get('driver_name')} -> {vehicle.get('vehicle_number')}"
                     f" ({start} to {end or 'ongoing'})", user["email"])
    return {"success": True, "id": assignment_id}


@router.put("/api/vehicles/assignments/{assignment_id}")
async def update_assignment(assignment_id: str, request: Request,
                            user=Depends(require_editor)):
    if not ss.find_record_by_id("vehicleassignments", assignment_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    data = await request.json()
    if data.get("driver_id"):
        driver = ss.find_record_by_id("drivers", data["driver_id"])
        if not driver:
            raise HTTPException(status_code=404, detail="Employee not found")
        data["driver_name"] = driver.get("driver_name")
    ss.update_record("vehicleassignments", assignment_id, data)
    ss.add_audit_log("Update", "Vehicles", assignment_id, "Updated assignment",
                     user["email"])
    return {"success": True}


@router.delete("/api/vehicles/assignments/{assignment_id}")
async def delete_assignment(assignment_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("vehicleassignments", assignment_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    ss.delete_record("vehicleassignments", assignment_id)
    ss.add_audit_log("Delete", "Vehicles", assignment_id, "Deleted assignment",
                     user["email"])
    return {"success": True}


# ------------------------------------------------------------------ detail data

@router.get("/api/vehicles/{vehicle_id}/expenses")
async def vehicle_expenses(vehicle_id: str, month: str = "",
                           user=Depends(require_view)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    number = vehicle.get("vehicle_number")
    rows = [e for e in ss.get_all_records("expenses")
            if e.get("vehicle_number") == number]
    fuel = [f for f in ss.get_all_records("fuelentries")
            if f.get("vehicle_number") == number]
    if month:
        rows = [e for e in rows if (e.get("expense_date") or "").startswith(month)]
        fuel = [f for f in fuel if (f.get("entry_date") or "").startswith(month)]
    rows.sort(key=lambda e: e.get("expense_date") or "", reverse=True)
    return {
        "expenses": rows,
        "total_expense": round(sum(ss.to_float(e.get("amount")) for e in rows), 2),
        "total_fuel_amount": round(sum(ss.to_float(f.get("amount")) for f in fuel), 2),
        "total_fuel_litres": round(sum(ss.to_float(f.get("litres")) for f in fuel), 2),
    }


@router.get("/api/vehicles/{vehicle_id}/documents")
async def vehicle_documents(vehicle_id: str, user=Depends(require_view)):
    return [d for d in ss.get_all_records("documents")
            if d.get("entity_type") == "vehicle" and d.get("entity_id") == vehicle_id]


@router.post("/api/vehicles/{vehicle_id}/upload")
async def upload_vehicle_document(vehicle_id: str, document_type: str = Form(...),
                                  file: UploadFile = File(...),
                                  user=Depends(require_editor)):
    from routes.documents import store_document
    return await store_document("vehicle", vehicle_id, document_type, file, user)


# ------------------------------------------------------------------ helpers

def _overlaps(start_a, end_a, start_b, end_b):
    """True when two (possibly open-ended) date ranges overlap."""
    start_b = start_b or ""
    if not start_a or not start_b:
        return False
    end_a = end_a or "9999-12-31"
    end_b = end_b or "9999-12-31"
    return start_a <= end_b and start_b <= end_a


def _sync_driver_assignment(vehicle_number, driver_name):
    """Keep drivers.assigned_vehicle in step with the vehicle's default driver."""
    if not vehicle_number:
        return False
    changed = False
    for driver in ss.get_all_records("drivers"):
        assigned = driver.get("assigned_vehicle") == vehicle_number
        is_default = bool(driver_name) and driver.get("driver_name") == driver_name
        if is_default and not assigned:
            ss.update_record("drivers", driver["driver_id"],
                             {"assigned_vehicle": vehicle_number})
            changed = True
        elif assigned and not is_default:
            ss.update_record("drivers", driver["driver_id"], {"assigned_vehicle": ""})
            changed = True
    return changed
