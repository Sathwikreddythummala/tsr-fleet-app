"""Fuel: diesel and consumable entries, plus the credit ("Katha") ledger.

An entry is Paid by default. Marking it Unpaid puts it on the station's Katha
until it is settled - the fuel station is then mandatory so the debt has an
owner.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils import duplicate_check
from utils.exports import excel_response, pdf_response
from utils.filters import filter_month, filter_multi, filter_range
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["fuel"])

FUEL_TYPES = ("Diesel", "Petrol", "CNG", "AdBlue", "Engine Oil", "Coolant", "Other")
PAYMENT_STATUSES = ("Paid", "Unpaid")


@router.get("/fuel")
async def fuel_page(request: Request, user=Depends(require_view)):
    return render(request, "fuel.html", {"fuel_types": list(FUEL_TYPES)})


@router.get("/api/fuel/list")
async def list_fuel(date_from: str = "", date_to: str = "", vehicle: str = "",
                    driver: str = "", fuel_type: str = "", payment_status: str = "",
                    station: str = "", month: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("fuelentries")
    rows = filter_month(rows, "entry_date", month)
    rows = filter_range(rows, "entry_date", date_from, date_to)
    rows = filter_multi(rows, "vehicle_number", vehicle)
    rows = filter_multi(rows, "driver_name", driver)
    rows = filter_multi(rows, "fuel_type", fuel_type, default="Diesel")
    rows = filter_multi(rows, "payment_status", payment_status, default="Paid")
    rows = filter_multi(rows, "fuel_station", station)
    rows.sort(key=lambda f: f.get("entry_date") or "", reverse=True)
    unpaid = [f for f in rows if (f.get("payment_status") or "Paid") == "Unpaid"]
    return {
        "rows": rows,
        "total_litres": round(sum(ss.to_float(f.get("litres")) for f in rows), 2),
        "total_amount": round(sum(ss.to_float(f.get("amount")) for f in rows), 2),
        "credit_outstanding": round(sum(ss.to_float(f.get("amount")) for f in unpaid), 2),
        "credit_count": len(unpaid),
    }


@router.post("/api/fuel/add")
async def add_fuel(request: Request, user=Depends(require_editor)):
    data = await request.json()
    return _create_entry(data, user, module="Fuel")


@router.put("/api/fuel/{fuel_id}")
async def update_fuel(fuel_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not ss.find_record_by_id("fuelentries", fuel_id):
        raise HTTPException(status_code=404, detail="Fuel entry not found")
    status = data.get("payment_status") or "Paid"
    if status == "Unpaid" and not (data.get("fuel_station") or "").strip():
        raise HTTPException(status_code=400,
                            detail="Fuel station is required for credit (Katha) entries")
    data["payment_status"] = status
    data["paid_date"] = ss.today_str() if status == "Paid" else ""
    if not data.get("force") and duplicate_check.check("fuelentries", data,
                                                       exclude_id=fuel_id):
        raise HTTPException(status_code=409, detail="A matching fuel entry already exists")
    data.pop("force", None)
    ss.update_record("fuelentries", fuel_id, data)
    ss.add_audit_log("Update", "Fuel", fuel_id, "Updated fuel entry", user["email"])
    return {"success": True}


@router.delete("/api/fuel/{fuel_id}")
async def delete_fuel(fuel_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("fuelentries", fuel_id):
        raise HTTPException(status_code=404, detail="Fuel entry not found")
    ss.delete_record("fuelentries", fuel_id)
    ss.add_audit_log("Delete", "Fuel", fuel_id, "Deleted fuel entry", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ credit ledger

@router.get("/api/fuel/credit")
async def credit_ledger(user=Depends(require_view)):
    """Unpaid fuel grouped by station - who we owe, and how much."""
    unpaid = [f for f in ss.get_all_records("fuelentries")
              if (f.get("payment_status") or "Paid") == "Unpaid"]
    stations = {}
    for entry in unpaid:
        name = (entry.get("fuel_station") or "Unknown station").strip()
        group = stations.setdefault(name, {"station": name, "entries": [],
                                           "amount": 0.0, "litres": 0.0})
        group["entries"].append(entry)
        group["amount"] += ss.to_float(entry.get("amount"))
        group["litres"] += ss.to_float(entry.get("litres"))
    rows = sorted(stations.values(), key=lambda g: -g["amount"])
    for group in rows:
        group["amount"] = round(group["amount"], 2)
        group["litres"] = round(group["litres"], 2)
        group["count"] = len(group["entries"])
        group["entries"].sort(key=lambda e: e.get("entry_date") or "", reverse=True)
        group["oldest"] = min((e.get("entry_date") or "" for e in group["entries"]),
                              default="")
    return {"rows": rows, "total_outstanding": round(sum(g["amount"] for g in rows), 2),
            "station_count": len(rows),
            "entry_count": sum(g["count"] for g in rows)}


@router.post("/api/fuel/settle")
async def settle_credit(request: Request, user=Depends(require_editor)):
    """Mark one entry, or an entire station's Katha, as paid."""
    data = await request.json()
    paid_date = data.get("paid_date") or ss.today_str()

    if data.get("fuel_id"):
        entry = ss.find_record_by_id("fuelentries", data["fuel_id"])
        if not entry:
            raise HTTPException(status_code=404, detail="Fuel entry not found")
        ss.update_record("fuelentries", entry["fuel_id"],
                         {"payment_status": "Paid", "paid_date": paid_date})
        ss.add_audit_log("Update", "Fuel", entry["fuel_id"],
                         f"Settled ₹{entry.get('amount')} at "
                         f"{entry.get('fuel_station')}", user["email"])
        return {"success": True, "settled": 1}

    station = (data.get("station") or "").strip()
    if not station:
        raise HTTPException(status_code=400, detail="Choose an entry or a station")
    settled, amount = 0, 0.0
    for entry in ss.get_all_records("fuelentries"):
        if ((entry.get("payment_status") or "Paid") == "Unpaid"
                and (entry.get("fuel_station") or "").strip() == station):
            ss.update_record("fuelentries", entry["fuel_id"],
                             {"payment_status": "Paid", "paid_date": paid_date})
            settled += 1
            amount += ss.to_float(entry.get("amount"))
    if not settled:
        raise HTTPException(status_code=404, detail=f"No unpaid entries for {station}")
    ss.add_audit_log("Update", "Fuel", station,
                     f"Settled {settled} entries (₹{round(amount, 2)}) at {station}",
                     user["email"])
    return {"success": True, "settled": settled, "amount": round(amount, 2)}


# ------------------------------------------------------------------ stats

@router.get("/api/fuel/stats")
async def fuel_stats(user=Depends(require_view)):
    rows = ss.get_all_records("fuelentries")
    monthly, by_vehicle, by_driver, by_station = {}, {}, {}, {}
    for f in rows:
        amount = ss.to_float(f.get("amount"))
        litres = ss.to_float(f.get("litres"))
        month = (f.get("entry_date") or "")[:7]
        if month:
            bucket = monthly.setdefault(month, {"amount": 0.0, "litres": 0.0})
            bucket["amount"] += amount
            bucket["litres"] += litres
        by_vehicle[f.get("vehicle_number") or "Unknown"] = \
            by_vehicle.get(f.get("vehicle_number") or "Unknown", 0) + amount
        by_driver[f.get("driver_name") or "Unknown"] = \
            by_driver.get(f.get("driver_name") or "Unknown", 0) + amount
        station = (f.get("fuel_station") or "").strip()
        if station:
            by_station[station] = by_station.get(station, 0) + amount
    months = sorted(monthly)[-6:]
    return {
        "months": months,
        "monthly_amount": [round(monthly[m]["amount"], 2) for m in months],
        "monthly_litres": [round(monthly[m]["litres"], 2) for m in months],
        "by_vehicle": dict(sorted(by_vehicle.items(), key=lambda x: -x[1])[:10]),
        "by_driver": dict(sorted(by_driver.items(), key=lambda x: -x[1])[:10]),
        "stations": sorted(by_station),
    }


@router.get("/api/fuel/export/excel")
async def export_excel(date_from: str = "", date_to: str = "", vehicle: str = "",
                       driver: str = "", fuel_type: str = "", payment_status: str = "",
                       station: str = "", month: str = "", user=Depends(require_view)):
    data = await list_fuel(date_from, date_to, vehicle, driver, fuel_type,
                           payment_status, station, month, user)
    headers = ["Date", "Vehicle", "Driver", "Type", "Litres", "Amount", "KM",
               "Station", "Mode", "Status", "Paid Date"]
    rows = [[f.get("entry_date"), f.get("vehicle_number"), f.get("driver_name"),
             f.get("fuel_type"), ss.to_float(f.get("litres")),
             ss.to_float(f.get("amount")), f.get("kilometre"), f.get("fuel_station"),
             f.get("payment_mode"), f.get("payment_status") or "Paid",
             f.get("paid_date")] for f in data["rows"]]
    totals = ["", "", "", "TOTAL", data["total_litres"], data["total_amount"],
              "", "", "", "", ""]
    return excel_response("Fuel", headers, rows, "fuel.xlsx", totals)


@router.get("/api/fuel/export/pdf")
async def export_pdf(date_from: str = "", date_to: str = "", vehicle: str = "",
                     driver: str = "", fuel_type: str = "", payment_status: str = "",
                     station: str = "", month: str = "", user=Depends(require_view)):
    data = await list_fuel(date_from, date_to, vehicle, driver, fuel_type,
                           payment_status, station, month, user)
    headers = ["Date", "Vehicle", "Driver", "Type", "Litres", "Amount", "Station",
               "Status"]
    rows = [[f.get("entry_date"), f.get("vehicle_number"), f.get("driver_name"),
             f.get("fuel_type"), f"{ss.to_float(f.get('litres')):,.2f}",
             f"{ss.to_float(f.get('amount')):,.2f}", (f.get("fuel_station") or "")[:22],
             f.get("payment_status") or "Paid"] for f in data["rows"]]
    totals = ["", "", "", "TOTAL", f"{data['total_litres']:,.2f}",
              f"{data['total_amount']:,.2f}", "", ""]
    return pdf_response("TSR Enterprises - Fuel Entries", headers, rows, "fuel.pdf",
                        totals)


# ------------------------------------------------------------------ shared

def _create_entry(data, user, module="Fuel"):
    """Validate and insert a fuel entry (used by the office and driver apps)."""
    if ss.to_float(data.get("amount")) <= 0 and ss.to_float(data.get("litres")) <= 0:
        raise HTTPException(status_code=400, detail="Enter litres or amount")
    status = data.get("payment_status") or "Paid"
    if status not in PAYMENT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid payment status")
    if status == "Unpaid" and not (data.get("fuel_station") or "").strip():
        raise HTTPException(status_code=400,
                            detail="Fuel station is required for credit (Katha) entries")
    force = bool(data.pop("force", False))
    if not force and duplicate_check.check("fuelentries", data):
        raise HTTPException(status_code=409, detail="A matching fuel entry already exists")

    data["payment_status"] = status
    data["paid_date"] = ss.today_str() if status == "Paid" else ""
    data.setdefault("entry_date", ss.today_str())
    data.setdefault("fuel_type", "Diesel")
    fuel_id = ss.add_record("fuelentries", data)
    ss.add_audit_log("Create", module, fuel_id,
                     f"{data.get('vehicle_number')} {data.get('litres')}L "
                     f"₹{data.get('amount')} ({status})",
                     user.get("email") or user.get("driver_name", ""))
    return {"success": True, "id": fuel_id, "payment_status": status}
