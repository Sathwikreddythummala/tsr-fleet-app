"""Vehicles module: CRUD, driver assignment sync, documents, expenses view."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from services import drive_service, sheets_service as ss
from utils.permissions import require_admin, require_admin_or_viewer
from utils.templates import render

router = APIRouter(tags=["vehicles"])


@router.get("/vehicles")
async def vehicles_page(request: Request, user=Depends(require_admin_or_viewer)):
    return render(request, "vehicles.html")


@router.get("/vehicles/{vehicle_id}")
async def vehicle_details_page(request: Request, vehicle_id: str,
                               user=Depends(require_admin_or_viewer)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return render(request, "vehicle_details.html", {"vehicle": vehicle})


@router.get("/api/vehicles")
async def list_vehicles(user=Depends(require_admin_or_viewer)):
    rows = ss.get_all_records("vehicles")
    return sorted(rows, key=lambda v: v.get("vehicle_number") or "")


@router.get("/api/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: str, user=Depends(require_admin_or_viewer)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.post("/api/vehicles")
async def add_vehicle(request: Request, user=Depends(require_admin)):
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
    ss.add_audit_log("Create", "Vehicles", vehicle_id, f"Added vehicle {number}", user["email"])
    return {"success": True, "id": vehicle_id}


@router.put("/api/vehicles/{vehicle_id}")
async def update_vehicle(vehicle_id: str, request: Request, user=Depends(require_admin)):
    data = await request.json()
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    ss.update_record("vehicles", vehicle_id, data)
    number = data.get("vehicle_number") or vehicle.get("vehicle_number")
    if "default_driver" in data:
        _sync_driver_assignment(number, data.get("default_driver"))
    ss.add_audit_log("Update", "Vehicles", vehicle_id, f"Updated vehicle {number}", user["email"])
    return {"success": True}


@router.delete("/api/vehicles/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, user=Depends(require_admin)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    ss.delete_record("vehicles", vehicle_id)
    ss.add_audit_log("Delete", "Vehicles", vehicle_id,
                     f"Deleted vehicle {vehicle.get('vehicle_number')}", user["email"])
    return {"success": True}


@router.get("/api/vehicles/{vehicle_id}/expenses")
async def vehicle_expenses(vehicle_id: str, user=Depends(require_admin_or_viewer)):
    vehicle = ss.find_record_by_id("vehicles", vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    number = vehicle.get("vehicle_number")
    rows = [e for e in ss.get_all_records("expenses") if e.get("vehicle_number") == number]
    fuel = [f for f in ss.get_all_records("fuelentries") if f.get("vehicle_number") == number]
    return {
        "expenses": sorted(rows, key=lambda e: e.get("expense_date") or "", reverse=True),
        "total_expense": sum(ss.to_float(e.get("amount")) for e in rows),
        "total_fuel_amount": sum(ss.to_float(f.get("amount")) for f in fuel),
        "total_fuel_litres": sum(ss.to_float(f.get("litres")) for f in fuel),
    }


@router.get("/api/vehicles/{vehicle_id}/documents")
async def vehicle_documents(vehicle_id: str, user=Depends(require_admin_or_viewer)):
    return [d for d in ss.get_all_records("documents")
            if d.get("entity_type") == "vehicle" and d.get("entity_id") == vehicle_id]


@router.post("/api/vehicles/{vehicle_id}/documents")
async def upload_vehicle_document(vehicle_id: str, document_type: str = Form(...),
                                  file: UploadFile = File(...), user=Depends(require_admin)):
    return await upload_entity_document("vehicle", vehicle_id, document_type, file, user)


async def upload_entity_document(entity_type, entity_id, document_type, file, user):
    """Shared Drive upload used by vehicles, drivers and mithi routes."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    try:
        url = drive_service.upload_document(
            entity_type, f"{entity_id}_{document_type}_{file.filename}",
            content, file.content_type or "application/octet-stream")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Drive upload failed: {exc}")
    ss.add_record("documents", {
        "entity_type": entity_type, "entity_id": entity_id,
        "document_type": document_type, "file_name": file.filename,
        "drive_url": url, "uploaded_date": ss.now_str(),
    })
    ss.add_audit_log("Upload", entity_type.title() + "s", entity_id,
                     f"Uploaded {document_type}: {file.filename}", user["email"])
    return {"success": True, "url": url}


def _sync_driver_assignment(vehicle_number, driver_name):
    """Keep drivers.assigned_vehicle in sync with the vehicle's default driver."""
    if not vehicle_number:
        return
    for driver in ss.get_all_records("drivers"):
        assigned = driver.get("assigned_vehicle") == vehicle_number
        is_default = driver_name and driver.get("driver_name") == driver_name
        if is_default and not assigned:
            ss.update_record("drivers", driver["driver_id"], {"assigned_vehicle": vehicle_number})
        elif assigned and not is_default:
            ss.update_record("drivers", driver["driver_id"], {"assigned_vehicle": ""})
