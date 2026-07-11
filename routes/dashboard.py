"""Dashboard page, summary calculations and expiry/due alerts."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from services import sheets_service as ss
from utils.permissions import require_admin_or_viewer
from utils.templates import render

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def home(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login")
    return RedirectResponse("/driver-portal" if user.get("role") == "driver" else "/dashboard")


@router.get("/dashboard")
async def dashboard_page(request: Request, user=Depends(require_admin_or_viewer)):
    return render(request, "dashboard.html")


@router.get("/api/dashboard/summary")
async def dashboard_summary(user=Depends(require_admin_or_viewer)):
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
    total_income = (sum(ss.to_float(i.get("amount")) for i in income)
                    + sum(ss.to_float(b.get("paid_amount")) for b in billing))

    purse_stats = {}
    for holder in ("TSR", "TRR"):
        entries = [p for p in purse if p.get("holder") == holder]
        given = sum(ss.to_float(p.get("amount")) for p in entries if p.get("type") == "Credit")
        spent = sum(ss.to_float(p.get("amount")) for p in entries if p.get("type") in ("Debit", "Expense"))
        spent += sum(ss.to_float(e.get("amount")) for e in expenses if e.get("paid_by") == holder)
        purse_stats[holder] = {"given": given, "spent": spent, "balance": given - spent}

    active_mithi = [m for m in mithi_persons if (m.get("status") or "Active") != "Closed"]
    mithi_collected = sum(ss.to_float(p.get("mithi_amount")) for p in mithi_payments)
    pending_mithi = 0.0
    pending_main = 0.0
    now = datetime.now()
    for person in active_mithi:
        pending_main += ss.to_float(person.get("main_amount"))
        monthly = ss.to_float(person.get("monthly_mithi_amount"))
        start = _parse_date(person.get("start_date"))
        if start and monthly:
            months_due = max(0, (now.year - start.year) * 12 + (now.month - start.month) + 1)
            paid = sum(ss.to_float(p.get("mithi_amount")) for p in mithi_payments
                       if p.get("mithi_person_id") == person.get("mithi_person_id"))
            pending_mithi += max(0.0, months_due * monthly - paid)

    recent = sorted(expenses, key=lambda e: e.get("expense_date") or "", reverse=True)[:8]

    # last 6 months expense vs income chart data
    months, expense_series, income_series = [], [], []
    for offset in range(5, -1, -1):
        ref = (now.replace(day=1) - timedelta(days=offset * 28)).strftime("%Y-%m")
        if ref in months:
            continue
        months.append(ref)
    months = sorted(set(months))[-6:]
    for m in months:
        expense_series.append(sum(ss.to_float(e.get("amount")) for e in expenses
                                  if (e.get("expense_date") or "").startswith(m)))
        income_series.append(sum(ss.to_float(r.get("amount")) for r in ss.get_all_records("receivables")
                                 if (r.get("receive_date") or "").startswith(m)))

    category_totals = {}
    for e in expenses:
        cat = e.get("category") or "Other"
        category_totals[cat] = category_totals.get(cat, 0) + ss.to_float(e.get("amount"))

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "total_balance": total_income - total_expense,
        "today_expense": sum(ss.to_float(e.get("amount")) for e in expenses
                             if e.get("expense_date") == today),
        "monthly_expense": sum(ss.to_float(e.get("amount")) for e in expenses
                               if (e.get("expense_date") or "").startswith(month)),
        "total_billing": sum(ss.to_float(b.get("total_amount")) for b in billing),
        "total_received": sum(ss.to_float(b.get("paid_amount")) for b in billing),
        "total_outstanding": sum(ss.to_float(b.get("balance_amount")) for b in billing),
        "total_fuel_litres": sum(ss.to_float(f.get("litres")) for f in fuel),
        "total_fuel_amount": sum(ss.to_float(f.get("amount")) for f in fuel),
        "active_vehicles": sum(1 for v in vehicles if (v.get("vehicle_status") or "Active") == "Active"),
        "active_drivers": sum(1 for d in drivers if (d.get("status") or "Active") == "Active"),
        "purse": purse_stats,
        "purse_balance": sum(s["balance"] for s in purse_stats.values()),
        "mithi_collected": mithi_collected,
        "mithi_pending": pending_mithi,
        "main_amount_pending": pending_main,
        "active_mithi_persons": len(active_mithi),
        "closed_mithi_persons": len(mithi_persons) - len(active_mithi),
        "recent_expenses": recent,
        "chart_months": months,
        "chart_expense": expense_series,
        "chart_income": income_series,
        "chart_categories": category_totals,
    }


@router.get("/api/dashboard/alerts")
async def dashboard_alerts(user=Depends(require_admin_or_viewer)):
    alerts = []
    now = datetime.now()
    soon = now + timedelta(days=30)

    def expiry_alert(date_str, label, entity, icon):
        date = _parse_date(date_str)
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
        num = v.get("vehicle_number") or v.get("vehicle_id")
        expiry_alert(v.get("rc_expiry"), "RC", num, "bi-card-text")
        expiry_alert(v.get("insurance_expiry_date"), "Insurance", num, "bi-shield-check")
        expiry_alert(v.get("permit_expiry_date"), "Permit", num, "bi-file-earmark-check")
        expiry_alert(v.get("fitness_expiry_date"), "Fitness", num, "bi-heart-pulse")
        expiry_alert(v.get("puc_expiry_date"), "PUC", num, "bi-cloud-haze2")

    for d in ss.get_all_records("drivers"):
        if (d.get("status") or "Active") == "Active":
            expiry_alert(d.get("license_expiry_date"), "License",
                         d.get("driver_name") or d.get("driver_id"), "bi-person-vcard")

    # EMI due within 7 days (vehicle loans + other EMIs)
    for v in ss.get_all_records("vehicles"):
        if (v.get("loan_available") or "").lower() in ("yes", "true") and v.get("emi_date"):
            _emi_due_alert(alerts, v.get("emi_date"),
                           f"{v.get('vehicle_number')} vehicle EMI", now)
    for e in ss.get_all_records("otheremis"):
        if (e.get("status") or "Active") == "Active" and e.get("emi_date"):
            _emi_due_alert(alerts, e.get("emi_date"), e.get("emi_name") or "EMI", now)

    # Mithi payment pending for the current month
    month = ss.this_month()
    payments = ss.get_all_records("mithipayments")
    for person in ss.get_all_records("mithipersons"):
        if (person.get("status") or "Active") == "Closed":
            continue
        paid = any(p.get("mithi_person_id") == person.get("mithi_person_id")
                   and p.get("payment_month") == month for p in payments)
        if not paid:
            alerts.append({"level": "warning", "icon": "bi-cash-coin",
                           "text": f"Mithi pending for {person.get('person_name')} ({month})"})

    return {"alerts": alerts, "count": len(alerts)}


def _emi_due_alert(alerts, emi_day, label, now):
    try:
        day = int(str(emi_day).strip())
    except ValueError:
        return
    days_left = day - now.day
    if 0 <= days_left <= 7:
        alerts.append({"level": "warning", "icon": "bi-bank",
                       "text": f"{label} due on day {day} of this month"})


def _parse_date(value):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value or "").strip()[:10], fmt)
        except ValueError:
            continue
    return None
