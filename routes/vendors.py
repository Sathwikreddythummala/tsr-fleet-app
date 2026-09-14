"""Vendors module: the companies you bill."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.filters import filter_multi, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["vendors"])


@router.get("/vendors")
async def vendors_page(request: Request, user=Depends(require_view)):
    return render(request, "vendors.html")


@router.get("/api/vendors/list")
async def list_vendors(status: str = "", search: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("vendors")
    rows = filter_multi(rows, "status", status, default="Active")
    rows = filter_search(rows, search,
                         ["vendor_name", "contact_person", "mobile_number", "gst_number"])
    rows.sort(key=lambda v: (v.get("vendor_name") or "").lower())
    return {"rows": rows, "total": len(rows)}


@router.post("/api/vendors/add")
async def add_vendor(request: Request, user=Depends(require_editor)):
    data = await request.json()
    name = (data.get("vendor_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Vendor name is required")
    if any((v.get("vendor_name") or "").strip().lower() == name.lower()
           for v in ss.get_all_records("vendors")):
        raise HTTPException(status_code=400, detail=f"{name} already exists")
    data["vendor_name"] = name
    data.setdefault("status", "Active")
    vendor_id = ss.add_record("vendors", data)
    ss.add_audit_log("Create", "Vendors", vendor_id, f"Added vendor {name}", user["email"])
    return {"success": True, "id": vendor_id}


@router.put("/api/vendors/{vendor_id}")
async def update_vendor(vendor_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    vendor = ss.find_record_by_id("vendors", vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    old_name = vendor.get("vendor_name")
    new_name = (data.get("vendor_name") or old_name or "").strip()
    ss.update_record("vendors", vendor_id, data)
    if new_name and new_name != old_name:
        _rename_vendor_everywhere(old_name, new_name)
    ss.add_audit_log("Update", "Vendors", vendor_id, f"Updated {new_name}", user["email"])
    return {"success": True}


@router.delete("/api/vendors/{vendor_id}")
async def delete_vendor(vendor_id: str, user=Depends(require_editor)):
    vendor = ss.find_record_by_id("vendors", vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    name = vendor.get("vendor_name")
    in_use = sum(1 for b in ss.get_all_records("billing") if b.get("vendor_name") == name)
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"{name} is used on {in_use} bill(s) - deactivate instead of deleting")
    ss.delete_record("vendors", vendor_id)
    ss.add_audit_log("Delete", "Vendors", vendor_id, f"Deleted vendor {name}", user["email"])
    return {"success": True}


def _rename_vendor_everywhere(old_name, new_name):
    """Keep billing, receivables and income in step with a vendor rename."""
    if not old_name:
        return
    for table, field, key in (("billing", "vendor_name", "bill_id"),
                              ("receivables", "vendor_name", "receivable_id"),
                              ("income", "vendor_name", "income_id")):
        for row in ss.get_all_records(table):
            if row.get(field) == old_name:
                ss.update_record(table, row[key], {field: new_name})
    for v in ss.get_all_records("vehicles"):
        if v.get("default_vendor") == old_name:
            ss.update_record("vehicles", v["vehicle_id"], {"default_vendor": new_name})
