"""Income module: trip-level revenue entries."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.exports import excel_response
from utils.filters import filter_month, filter_multi, filter_range, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["income"])

UNITS = ("Trip", "Ton", "Cum", "Km", "Hour", "Load", "Other")
PAYMENT_STATUSES = ("Pending", "Partial", "Paid")


@router.get("/income")
async def income_page(request: Request, user=Depends(require_view)):
    return render(request, "income.html", {"units": list(UNITS)})


@router.get("/api/income/list")
async def list_income(date_from: str = "", date_to: str = "", vehicle: str = "",
                      vendor: str = "", status: str = "", search: str = "",
                      month: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("income")
    rows = filter_month(rows, "income_date", month)
    rows = filter_range(rows, "income_date", date_from, date_to)
    rows = filter_multi(rows, "vehicle_number", vehicle)
    rows = filter_multi(rows, "vendor_name", vendor)
    rows = filter_multi(rows, "payment_status", status, default="Pending")
    rows = filter_search(rows, search, ["trip_from", "trip_to", "material",
                                        "invoice_number", "description",
                                        "driver_name", "vehicle_number"])
    rows.sort(key=lambda i: i.get("income_date") or "", reverse=True)
    total = sum(ss.to_float(i.get("amount")) for i in rows)
    received = sum(ss.to_float(i.get("amount")) for i in rows
                   if i.get("payment_status") == "Paid")
    return {"rows": rows, "total": len(rows), "total_amount": round(total, 2),
            "received": round(received, 2), "pending": round(total - received, 2)}


@router.get("/api/income/stats")
async def income_stats(user=Depends(require_view)):
    rows = ss.get_all_records("income")
    monthly, by_vehicle = {}, {}
    for i in rows:
        amount = ss.to_float(i.get("amount"))
        month = (i.get("income_date") or "")[:7]
        if month:
            monthly[month] = monthly.get(month, 0) + amount
        vehicle = i.get("vehicle_number") or "Unassigned"
        by_vehicle[vehicle] = by_vehicle.get(vehicle, 0) + amount
    months = sorted(monthly)[-6:]
    return {
        "months": months,
        "monthly_amount": [round(monthly[m], 2) for m in months],
        "by_vehicle": dict(sorted(by_vehicle.items(), key=lambda x: -x[1])[:10]),
        "this_month": round(monthly.get(ss.this_month(), 0), 2),
    }


@router.post("/api/income/add")
async def add_income(request: Request, user=Depends(require_editor)):
    data = await request.json()
    amount = ss.to_float(data.get("amount"))
    if amount <= 0:
        quantity = ss.to_float(data.get("quantity"))
        rate = ss.to_float(data.get("rate"))
        amount = round(quantity * rate, 2)
        if amount <= 0:
            raise HTTPException(status_code=400,
                                detail="Enter an amount, or quantity and rate")
        data["amount"] = amount
    data.setdefault("income_date", ss.today_str())
    data.setdefault("payment_status", "Pending")
    income_id = ss.add_record("income", data)
    ss.add_audit_log("Create", "Income", income_id,
                     f"₹{amount} from {data.get('vendor_name') or 'trip'}", user["email"])
    return {"success": True, "id": income_id}


@router.put("/api/income/{income_id}")
async def update_income(income_id: str, request: Request, user=Depends(require_editor)):
    if not ss.find_record_by_id("income", income_id):
        raise HTTPException(status_code=404, detail="Income entry not found")
    data = await request.json()
    if ss.to_float(data.get("amount")) <= 0:
        data["amount"] = round(ss.to_float(data.get("quantity"))
                               * ss.to_float(data.get("rate")), 2)
    if data.get("payment_status") == "Paid" and not data.get("payment_date"):
        data["payment_date"] = ss.today_str()
    ss.update_record("income", income_id, data)
    ss.add_audit_log("Update", "Income", income_id, "Updated income entry", user["email"])
    return {"success": True}


@router.delete("/api/income/{income_id}")
async def delete_income(income_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("income", income_id):
        raise HTTPException(status_code=404, detail="Income entry not found")
    ss.delete_record("income", income_id)
    ss.add_audit_log("Delete", "Income", income_id, "Deleted income entry", user["email"])
    return {"success": True}


@router.get("/api/income/export/excel")
async def export_income(date_from: str = "", date_to: str = "", vehicle: str = "",
                        vendor: str = "", status: str = "", search: str = "",
                        month: str = "", user=Depends(require_view)):
    data = await list_income(date_from, date_to, vehicle, vendor, status, search,
                             month, user)
    headers = ["Date", "Vehicle", "Driver", "Customer", "From", "To", "Material",
               "Qty", "Unit", "Rate", "Amount", "Invoice", "Status"]
    rows = [[i.get("income_date"), i.get("vehicle_number"), i.get("driver_name"),
             i.get("vendor_name"), i.get("trip_from"), i.get("trip_to"),
             i.get("material"), ss.to_float(i.get("quantity")), i.get("unit"),
             ss.to_float(i.get("rate")), ss.to_float(i.get("amount")),
             i.get("invoice_number"), i.get("payment_status")]
            for i in data["rows"]]
    totals = ["", "", "", "", "", "", "", "", "", "TOTAL", data["total_amount"], "", ""]
    return excel_response("Income", headers, rows, "income.xlsx", totals)
