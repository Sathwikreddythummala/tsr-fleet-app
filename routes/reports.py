"""Reports: per-vehicle Profit & Loss and fuel mileage.

P&L model - a vehicle works in one month and is billed the next, so the
default view pairs expense month M with billing month M+1. Revenue is the
billing sub total (before GST). Outside (hired) vehicles are excluded.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.exports import excel_response, pdf_response
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["reports"])

FUEL_CATEGORIES = {"Fuel"}
DRIVER_CATEGORIES = {"Driver Expense", "Helper Expense"}
EMI_CATEGORIES = {"EMI"}


@router.get("/reports")
async def reports_page(request: Request, user=Depends(require_view)):
    expense_month = ss.shift_month(ss.this_month(), -1)
    return render(request, "reports.html", {
        "default_expense_month": expense_month,
        "default_billing_month": ss.shift_month(expense_month, 1),
    })


# ------------------------------------------------------------------ vehicle P&L

def _vehicle_pl(expense_month, billing_month):
    expense_month = expense_month or ss.shift_month(ss.this_month(), -1)
    billing_month = billing_month or ss.shift_month(expense_month, 1)

    expenses = [e for e in ss.get_all_records("expenses")
                if (e.get("expense_date") or "").startswith(expense_month)]
    bills = [b for b in ss.get_all_records("billing")
             if (b.get("payment_month") or "") == billing_month]
    incomes = [i for i in ss.get_all_records("income")
               if (i.get("income_date") or "").startswith(billing_month)]

    rows = []
    for vehicle in sorted(ss.get_all_records("vehicles"),
                          key=lambda v: (v.get("vehicle_number") or "").upper()):
        number = vehicle.get("vehicle_number")
        own_expenses = [e for e in expenses if e.get("vehicle_number") == number]
        fuel = sum(ss.to_float(e.get("amount")) for e in own_expenses
                   if e.get("category") in FUEL_CATEGORIES)
        driver = sum(ss.to_float(e.get("amount")) for e in own_expenses
                     if e.get("category") in DRIVER_CATEGORIES)
        emi = sum(ss.to_float(e.get("amount")) for e in own_expenses
                  if e.get("category") in EMI_CATEGORIES)
        other = sum(ss.to_float(e.get("amount")) for e in own_expenses
                    if e.get("category") not in
                    FUEL_CATEGORIES | DRIVER_CATEGORIES | EMI_CATEGORIES)
        total_expense = fuel + driver + emi + other
        revenue = sum(ss.to_float(b.get("sub_total")) for b in bills
                      if b.get("vehicle_number") == number)
        revenue += sum(ss.to_float(i.get("amount")) for i in incomes
                       if i.get("vehicle_number") == number)
        profit = revenue - total_expense
        rows.append({
            "vehicle_number": number,
            "vehicle_type": vehicle.get("vehicle_type") or "",
            "status": vehicle.get("vehicle_status") or "Active",
            "revenue": round(revenue, 2),
            "fuel": round(fuel, 2),
            "driver": round(driver, 2),
            "emi": round(emi, 2),
            "other": round(other, 2),
            "total_expense": round(total_expense, 2),
            "profit": round(profit, 2),
            "margin": round(profit / revenue * 100, 1) if revenue else 0.0,
        })

    totals = {key: round(sum(r[key] for r in rows), 2) for key in
              ("revenue", "fuel", "driver", "emi", "other", "total_expense", "profit")}
    totals["margin"] = (round(totals["profit"] / totals["revenue"] * 100, 1)
                        if totals["revenue"] else 0.0)
    return {"expense_month": expense_month, "billing_month": billing_month,
            "expense_month_name": ss.month_name(expense_month),
            "billing_month_name": ss.month_name(billing_month),
            "rows": rows, "totals": totals}


@router.get("/api/reports/vehicle-pl")
async def vehicle_pl(expense_month: str = "", billing_month: str = "",
                     user=Depends(require_view)):
    return _vehicle_pl(expense_month, billing_month)


@router.get("/api/reports/vehicle-pl/excel")
async def vehicle_pl_excel(expense_month: str = "", billing_month: str = "",
                           user=Depends(require_view)):
    data = _vehicle_pl(expense_month, billing_month)
    headers = ["Vehicle", "Type", "Revenue", "Fuel", "Driver", "EMI", "Other",
               "Total Expense", "Profit / Loss", "Margin %"]
    rows = [[r["vehicle_number"], r["vehicle_type"], r["revenue"], r["fuel"],
             r["driver"], r["emi"], r["other"], r["total_expense"], r["profit"],
             r["margin"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", t["revenue"], t["fuel"], t["driver"], t["emi"],
              t["other"], t["total_expense"], t["profit"], t["margin"]]
    return excel_response(f"P&L {data['expense_month']}", headers, rows,
                          f"vehicle_pl_{data['expense_month']}.xlsx", totals)


@router.get("/api/reports/vehicle-pl/pdf")
async def vehicle_pl_pdf(expense_month: str = "", billing_month: str = "",
                         user=Depends(require_view)):
    data = _vehicle_pl(expense_month, billing_month)
    headers = ["Vehicle", "Revenue", "Fuel", "Driver", "EMI", "Other",
               "Total Exp.", "P&L", "Margin %"]
    rows = [[r["vehicle_number"], f"{r['revenue']:,.0f}", f"{r['fuel']:,.0f}",
             f"{r['driver']:,.0f}", f"{r['emi']:,.0f}", f"{r['other']:,.0f}",
             f"{r['total_expense']:,.0f}", f"{r['profit']:,.0f}", f"{r['margin']}%"]
            for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", f"{t['revenue']:,.0f}", f"{t['fuel']:,.0f}",
              f"{t['driver']:,.0f}", f"{t['emi']:,.0f}", f"{t['other']:,.0f}",
              f"{t['total_expense']:,.0f}", f"{t['profit']:,.0f}", f"{t['margin']}%"]
    title = (f"TSR Enterprises - Vehicle P&amp;L · Expenses {data['expense_month_name']}"
             f" · Billing {data['billing_month_name']}")
    return pdf_response(title, headers, rows,
                        f"vehicle_pl_{data['expense_month']}.pdf", totals)


# ------------------------------------------------------------------ mileage

def _mileage(month):
    month = month or ss.shift_month(ss.this_month(), -1)
    fuel_rows = [f for f in ss.get_all_records("fuelentries")
                 if (f.get("entry_date") or "").startswith(month)
                 and (f.get("fuel_type") or "Diesel") == "Diesel"]
    saved = {r.get("vehicle_number"): r for r in ss.get_all_records("mileagereports")
             if r.get("month") == month}

    rows = []
    for vehicle in sorted(ss.get_all_records("vehicles"),
                          key=lambda v: (v.get("vehicle_number") or "").upper()):
        if (vehicle.get("vehicle_status") or "Active") != "Active":
            continue
        number = vehicle.get("vehicle_number")
        litres = sum(ss.to_float(f.get("litres")) for f in fuel_rows
                     if f.get("vehicle_number") == number)
        record = saved.get(number, {})
        stored_litres = ss.to_float(record.get("litres"))
        use_litres = stored_litres if stored_litres > 0 else litres
        approved_km = ss.to_float(record.get("approved_km"))
        rows.append({
            "vehicle_number": number,
            "vehicle_type": vehicle.get("vehicle_type") or "",
            "litres": round(use_litres, 2),
            "fuel_litres": round(litres, 2),
            "approved_km": approved_km,
            "mileage": round(approved_km / use_litres, 2) if use_litres else 0.0,
            "mileage_id": record.get("mileage_id", ""),
        })
    total_litres = sum(r["litres"] for r in rows)
    total_km = sum(r["approved_km"] for r in rows)
    return {"month": month, "month_name": ss.month_name(month), "rows": rows,
            "total_litres": round(total_litres, 2), "total_km": round(total_km, 2),
            "avg_mileage": round(total_km / total_litres, 2) if total_litres else 0.0}


@router.get("/api/reports/mileage")
async def mileage(month: str = "", user=Depends(require_view)):
    return _mileage(month)


@router.post("/api/reports/mileage/save")
async def save_mileage(request: Request, user=Depends(require_editor)):
    data = await request.json()
    month = data.get("month") or ss.shift_month(ss.this_month(), -1)
    number = (data.get("vehicle_number") or "").strip().upper()
    if not number:
        raise HTTPException(status_code=400, detail="Vehicle number is required")
    payload = {
        "month": month, "vehicle_number": number,
        "approved_km": ss.to_float(data.get("approved_km")),
        "litres": ss.to_float(data.get("litres")),
        "updated_by": user["email"], "updated_date": ss.now_str(),
    }
    existing = next((r for r in ss.get_all_records("mileagereports")
                     if r.get("month") == month and r.get("vehicle_number") == number),
                    None)
    if existing:
        ss.update_record("mileagereports", existing["mileage_id"], payload)
        mileage_id = existing["mileage_id"]
    else:
        mileage_id = ss.add_record("mileagereports", payload)
    return {"success": True, "id": mileage_id}


@router.get("/api/reports/mileage/excel")
async def mileage_excel(month: str = "", user=Depends(require_view)):
    data = _mileage(month)
    headers = ["Vehicle", "Type", "Diesel Litres", "Approved KM", "Mileage (km/l)"]
    rows = [[r["vehicle_number"], r["vehicle_type"], r["litres"], r["approved_km"],
             r["mileage"]] for r in data["rows"]]
    totals = ["TOTAL", "", data["total_litres"], data["total_km"], data["avg_mileage"]]
    return excel_response(f"Mileage {data['month']}", headers, rows,
                          f"mileage_{data['month']}.xlsx", totals)


@router.get("/api/reports/mileage/pdf")
async def mileage_pdf(month: str = "", user=Depends(require_view)):
    data = _mileage(month)
    headers = ["Vehicle", "Type", "Diesel Litres", "Approved KM", "Mileage (km/l)"]
    rows = [[r["vehicle_number"], r["vehicle_type"], f"{r['litres']:,.2f}",
             f"{r['approved_km']:,.0f}", f"{r['mileage']:,.2f}"]
            for r in data["rows"]]
    totals = ["TOTAL", "", f"{data['total_litres']:,.2f}",
              f"{data['total_km']:,.0f}", f"{data['avg_mileage']:,.2f}"]
    return pdf_response(f"TSR Enterprises - Fuel Mileage · {data['month_name']}",
                        headers, rows, f"mileage_{data['month']}.pdf", totals,
                        landscape_mode=False)
