"""Dashboard: KPIs, charts, recent activity and the alerts feed."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from services import sheets_service as ss
from utils.permissions import require_view
from utils.templates import render

router = APIRouter(tags=["dashboard"])

EXPIRY_WINDOW_DAYS = 30
EMI_WINDOW_DAYS = 7


@router.get("/")
async def home(request: Request):
    if request.session.get("portal_driver") and not request.session.get("user"):
        return RedirectResponse("/driver-portal")
    if not request.session.get("user"):
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")


@router.get("/dashboard")
async def dashboard_page(request: Request, user=Depends(require_view)):
    return render(request, "dashboard.html")


@router.get("/api/dashboard/stats")
async def dashboard_stats(user=Depends(require_view)):
    today = ss.today_str()
    month = ss.this_month()

    expenses = ss.get_all_records("expenses")
    income = ss.get_all_records("income")
    billing = ss.get_all_records("billing")
    fuel = ss.get_all_records("fuelentries")
    vehicles = ss.get_all_records("vehicles")
    drivers = ss.get_all_records("drivers")
    purse = ss.get_all_records("purse")
    mithi_persons = ss.get_all_records("mithipersons")
    mithi_payments = ss.get_all_records("mithipayments")

    total_expense = sum(ss.to_float(e.get("amount")) for e in expenses)
    house_expense = sum(ss.to_float(e.get("amount")) for e in expenses
                        if e.get("category") == "House Expense")
    received = sum(ss.to_float(b.get("paid_amount")) for b in billing)
    total_income = received + sum(ss.to_float(i.get("amount")) for i in income)

    purse_stats = {}
    linked = {p.get("reference_id") for p in purse if p.get("reference_id")}
    for holder in ("TSR", "TRR"):
        entries = [p for p in purse if p.get("holder") == holder]
        given = sum(ss.to_float(p.get("amount")) for p in entries
                    if p.get("type") in ("Credit", "In"))
        spent = sum(ss.to_float(p.get("amount")) for p in entries
                    if p.get("type") in ("Debit", "Out", "Expense"))
        spent += sum(ss.to_float(e.get("amount")) for e in expenses
                     if e.get("paid_by") == holder
                     and e.get("expense_id") not in linked)
        purse_stats[holder] = {"given": round(given, 2), "spent": round(spent, 2),
                               "balance": round(given - spent, 2)}

    active_mithi = [m for m in mithi_persons if (m.get("status") or "Active") != "Closed"]
    pending_mithi = pending_main = 0.0
    now = datetime.now(ss.IST).replace(tzinfo=None)
    from routes.mithi import accrual_start
    for person in active_mithi:
        pending_main += ss.to_float(person.get("main_amount"))
        monthly = ss.to_float(person.get("monthly_mithi_amount"))
        accrual = accrual_start(person)
        if accrual and monthly:
            year, month = accrual
            months_due = max(0, (now.year - year) * 12 + (now.month - month) + 1)
            paid = sum(ss.to_float(p.get("mithi_amount")) for p in mithi_payments
                       if p.get("mithi_person_id") == person.get("mithi_person_id"))
            pending_mithi += max(0.0, months_due * monthly - paid)

    unpaid_fuel = [f for f in fuel if (f.get("payment_status") or "Paid") == "Unpaid"]

    return {
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "total_balance": round(total_income - total_expense, 2),
        "today_expense": round(sum(ss.to_float(e.get("amount")) for e in expenses
                                   if e.get("expense_date") == today), 2),
        "monthly_expense": round(sum(ss.to_float(e.get("amount")) for e in expenses
                                     if (e.get("expense_date") or "").startswith(month)), 2),
        "house_expense": round(house_expense, 2),
        "house_expense_month": round(sum(ss.to_float(e.get("amount")) for e in expenses
                                         if e.get("category") == "House Expense"
                                         and (e.get("expense_date") or "").startswith(month)), 2),
        "total_billing": round(sum(ss.to_float(b.get("total_amount")) for b in billing), 2),
        "total_received": round(received, 2),
        "total_outstanding": round(sum(ss.to_float(b.get("balance_amount"))
                                       for b in billing), 2),
        "trip_income": round(sum(ss.to_float(i.get("amount")) for i in income), 2),
        "total_fuel_litres": round(sum(ss.to_float(f.get("litres")) for f in fuel), 2),
        "total_fuel_amount": round(sum(ss.to_float(f.get("amount")) for f in fuel), 2),
        "fuel_credit": round(sum(ss.to_float(f.get("amount")) for f in unpaid_fuel), 2),
        "active_vehicles": sum(1 for v in vehicles
                               if (v.get("vehicle_status") or "Active") == "Active"),
        "active_drivers": sum(1 for d in drivers
                              if (d.get("status") or "Active") == "Active"),
        "purse": purse_stats,
        "purse_balance": round(sum(s["balance"] for s in purse_stats.values()), 2),
        "mithi_collected": round(sum(ss.to_float(p.get("mithi_amount"))
                                     for p in mithi_payments), 2),
        "mithi_pending": round(pending_mithi, 2),
        "main_amount_pending": round(pending_main, 2),
        "active_mithi_persons": len(active_mithi),
        "closed_mithi_persons": len(mithi_persons) - len(active_mithi),
    }


# the original endpoint name is kept so older bookmarks keep working
@router.get("/api/dashboard/summary")
async def dashboard_summary(user=Depends(require_view)):
    stats = await dashboard_stats(user)
    charts = await dashboard_charts(user)
    recent = await recent_expenses(user=user)
    return {**stats, **charts, "recent_expenses": recent["rows"]}


@router.get("/api/dashboard/charts")
async def dashboard_charts(user=Depends(require_view)):
    expenses = ss.get_all_records("expenses")
    receivables = ss.get_all_records("receivables")
    income = ss.get_all_records("income")

    months = []
    current = ss.this_month()
    for offset in range(5, -1, -1):
        months.append(ss.shift_month(current, -offset))

    expense_series, income_series = [], []
    for m in months:
        expense_series.append(round(sum(ss.to_float(e.get("amount")) for e in expenses
                                        if (e.get("expense_date") or "").startswith(m)), 2))
        revenue = sum(ss.to_float(r.get("amount")) for r in receivables
                      if (r.get("receive_date") or "").startswith(m))
        revenue += sum(ss.to_float(i.get("amount")) for i in income
                       if (i.get("income_date") or "").startswith(m))
        income_series.append(round(revenue, 2))

    categories = {}
    for e in expenses:
        key = e.get("category") or "Other"
        categories[key] = categories.get(key, 0) + ss.to_float(e.get("amount"))

    return {
        "chart_months": months,
        "chart_month_names": [ss.month_name(m) for m in months],
        "chart_expense": expense_series,
        "chart_income": income_series,
        "chart_categories": {k: round(v, 2) for k, v in
                             sorted(categories.items(), key=lambda x: -x[1])[:8]},
    }


@router.get("/api/dashboard/recent-expenses")
async def recent_expenses(limit: int = 8, user=Depends(require_view)):
    rows = sorted(ss.get_all_records("expenses"),
                  key=lambda e: (e.get("expense_date") or "",
                                 e.get("created_date") or ""), reverse=True)
    return {"rows": rows[:max(1, min(limit, 50))]}


@router.get("/api/dashboard/alerts")
async def dashboard_alerts(user=Depends(require_view)):
    alerts = []
    now = datetime.now(ss.IST).replace(tzinfo=None)
    soon = now + timedelta(days=EXPIRY_WINDOW_DAYS)

    def expiry_alert(date_str, label, entity, icon):
        date = ss.parse_date(date_str)
        if not date:
            return
        if date < now:
            alerts.append({"level": "danger", "icon": icon,
                           "text": f"{entity}: {label} expired on {date_str}"})
        elif date <= soon:
            alerts.append({"level": "warning", "icon": icon,
                           "text": f"{entity}: {label} expires on {date_str}"})

    for v in ss.get_all_records("vehicles"):
        if (v.get("vehicle_status") or "Active") != "Active":
            continue
        number = v.get("vehicle_number") or v.get("vehicle_id")
        expiry_alert(v.get("rc_expiry"), "RC", number, "bi-card-text")
        expiry_alert(v.get("insurance_expiry_date"), "Insurance", number,
                     "bi-shield-check")
        expiry_alert(v.get("permit_expiry_date"), "Permit", number,
                     "bi-file-earmark-check")
        expiry_alert(v.get("fitness_expiry_date"), "Fitness", number, "bi-heart-pulse")
        expiry_alert(v.get("puc_expiry_date"), "PUC", number, "bi-cloud-haze2")
        if (v.get("loan_available") or "").lower() in ("yes", "true") and v.get("emi_date"):
            _emi_due_alert(alerts, v.get("emi_date"),
                           f"{v.get('vehicle_number')} vehicle EMI", now)

    for d in ss.get_all_records("drivers"):
        if (d.get("status") or "Active") == "Active":
            expiry_alert(d.get("license_expiry_date"), "License",
                         d.get("driver_name") or d.get("driver_id"), "bi-person-vcard")

    for e in ss.get_all_records("otheremis"):
        if (e.get("status") or "Active") == "Active" and e.get("emi_date"):
            _emi_due_alert(alerts, e.get("emi_date"), e.get("emi_name") or "EMI", now)

    month = ss.this_month()
    payments = ss.get_all_records("mithipayments")
    for person in ss.get_all_records("mithipersons"):
        if (person.get("status") or "Active") == "Closed":
            continue
        paid = any(p.get("mithi_person_id") == person.get("mithi_person_id")
                   and p.get("payment_month") == month for p in payments)
        if not paid:
            alerts.append({"level": "warning", "icon": "bi-cash-coin",
                           "text": f"Mithi pending for {person.get('person_name')}"
                                   f" ({ss.month_name(month)})"})

    credit = sum(ss.to_float(f.get("amount")) for f in ss.get_all_records("fuelentries")
                 if (f.get("payment_status") or "Paid") == "Unpaid")
    if credit > 0:
        alerts.append({"level": "warning", "icon": "bi-fuel-pump",
                       "text": f"Fuel credit (Katha) outstanding: ₹{credit:,.0f}"})

    return {"alerts": alerts, "count": len(alerts)}


def _emi_due_alert(alerts, emi_day, label, now):
    try:
        day = int(str(emi_day).strip())
    except (ValueError, TypeError):
        return
    days_left = day - now.day
    if 0 <= days_left <= EMI_WINDOW_DAYS:
        alerts.append({"level": "warning", "icon": "bi-bank",
                       "text": f"{label} due on day {day} of this month"})
