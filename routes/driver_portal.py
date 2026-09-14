"""Driver app: mobile + PIN self-service portal, installable as a PWA.

Drivers never enter the Google/role system - they get their own
`portal_driver` session keyed on the employee record.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from services import sheets_service as ss
from utils.permissions import require_driver, require_editor
from utils.templates import render

router = APIRouter(tags=["driver_portal"])

DRIVER_CATEGORY = "Driver Expense"
COMPONENT_LABELS = {"Salary": "Salary", "Advance": "Advance", "Meals": "Meals",
                    "Deductions": "Deduction", "Other": "Other"}


def clean_mobile(mobile):
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


# ------------------------------------------------------------------ pages

@router.get("/driver-portal")
async def driver_portal_page(request: Request):
    driver = request.session.get("portal_driver")
    if not driver:
        return render(request, "driver_login.html")
    record = ss.find_record_by_id("drivers", driver.get("driver_id", ""))
    if not record or (record.get("status") or "Active") != "Active":
        request.session.pop("portal_driver", None)
        return render(request, "driver_login.html",
                      {"message": "Your account is no longer active"})
    return render(request, "driver_portal.html", {"driver": record})


@router.get("/driver-portal/manage")
async def manage_page(request: Request, user=Depends(require_editor)):
    base = str(request.base_url).rstrip("/")
    return render(request, "driver_manage.html",
                  {"install_url": f"{base}/driver-portal"})


@router.get("/driver-portal/logout")
async def driver_logout(request: Request):
    driver = request.session.get("portal_driver") or {}
    if driver:
        ss.add_audit_log("Logout", "DriverApp", driver.get("driver_id"),
                         f"{driver.get('driver_name')} logged out", "")
    request.session.pop("portal_driver", None)
    return RedirectResponse("/driver-portal")


# ------------------------------------------------------------------ auth

@router.post("/api/driver-portal/login")
async def driver_login(request: Request):
    """Mobile number + PIN. Mobile matches on the last 10 digits."""
    body = await request.json()
    mobile = clean_mobile(body.get("mobile"))
    pin = str(body.get("pin") or "").strip()
    if not mobile or not pin:
        return JSONResponse({"success": False,
                             "message": "Enter your mobile number and PIN"},
                            status_code=400)

    driver = next((d for d in ss.get_all_records("drivers")
                   if clean_mobile(d.get("mobile_number")) == mobile), None)
    if not driver or (driver.get("status") or "Active") != "Active":
        return JSONResponse({"success": False,
                             "message": "This mobile number is not registered"},
                            status_code=403)
    stored = (driver.get("portal_pin") or "").strip()
    if not stored:
        return JSONResponse(
            {"success": False,
             "message": "No PIN set yet - ask the office to set your PIN"},
            status_code=403)
    if stored != pin:
        return JSONResponse({"success": False, "message": "Incorrect PIN"},
                            status_code=401)

    request.session["portal_driver"] = {
        "driver_id": driver.get("driver_id"),
        "driver_name": driver.get("driver_name"),
        "mobile_number": driver.get("mobile_number"),
    }
    ss.add_audit_log("Login", "DriverApp", driver.get("driver_id"),
                     f"{driver.get('driver_name')} signed in", driver.get("email", ""))
    return {"success": True, "redirect": "/driver-portal"}


# ------------------------------------------------------------------ data

@router.get("/api/driver-portal/my-data")
async def my_data(month: str = "", session_driver=Depends(require_driver)):
    driver = ss.find_record_by_id("drivers", session_driver.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee record not found")
    month = month or ss.this_month()
    name_key = (driver.get("driver_name") or "").strip().lower()

    components = [e for e in ss.get_all_records("expenses")
                  if e.get("category") == DRIVER_CATEGORY
                  and e.get("for_month") == month
                  and (e.get("driver_name") or "").strip().lower() == name_key]
    diesel = [f for f in ss.get_all_records("fuelentries")
              if (f.get("driver_name") or "").strip().lower() == name_key
              and (f.get("entry_date") or "").startswith(month)]
    diesel.sort(key=lambda d: d.get("entry_date") or "", reverse=True)

    def total(component):
        return round(sum(ss.to_float(e.get("amount")) for e in components
                         if e.get("sub_category") == component), 2)

    from routes.drivers import _salary_engine
    try:
        summary = next((r for r in _salary_engine(month)["rows"]
                        if r["driver_id"] == driver.get("driver_id")), None)
    except Exception:
        summary = None

    vehicle = next((v for v in ss.get_all_records("vehicles")
                    if v.get("vehicle_number") == driver.get("assigned_vehicle")), None)

    return {
        "month": month,
        "month_name": ss.month_name(month),
        "driver": {
            "driver_name": driver.get("driver_name"),
            "employee_type": driver.get("employee_type") or "Driver",
            "mobile_number": driver.get("mobile_number"),
            "assigned_vehicle": driver.get("assigned_vehicle") or "",
            "joining_date": driver.get("joining_date") or "",
        },
        "vehicle": {"vehicle_number": (vehicle or {}).get("vehicle_number", ""),
                    "vehicle_type": (vehicle or {}).get("vehicle_type", "")}
        if vehicle else None,
        "gross": (summary or {}).get("gross", ss.to_float(driver.get("salary"))),
        "net_payable": (summary or {}).get("net_payable", 0.0),
        "effective_days": (summary or {}).get("effective_days", 0.0),
        "total_salary": total("Salary"),
        "total_advance": total("Advance"),
        "total_meals": total("Meals"),
        "total_deduction": total("Deductions"),
        "entries": {
            label: sorted([e for e in components if e.get("sub_category") == key],
                          key=lambda e: e.get("expense_date") or "", reverse=True)
            for key, label in COMPONENT_LABELS.items()
        },
        "diesel_entries": diesel,
        "total_diesel_litres": round(sum(ss.to_float(d.get("litres"))
                                         for d in diesel), 2),
        "total_diesel_amount": round(sum(ss.to_float(d.get("amount"))
                                         for d in diesel), 2),
        "katha_count": sum(1 for d in diesel
                           if (d.get("payment_status") or "Paid") == "Unpaid"),
    }


@router.post("/api/driver-portal/diesel")
async def add_diesel(request: Request, session_driver=Depends(require_driver)):
    """Drivers log their own fuel, paid or on the station's Katha."""
    driver = ss.find_record_by_id("drivers", session_driver.get("driver_id", ""))
    if not driver:
        raise HTTPException(status_code=404, detail="Employee record not found")
    data = await request.json()
    entry = {
        "entry_date": data.get("entry_date") or ss.today_str(),
        "vehicle_number": (data.get("vehicle_number")
                           or driver.get("assigned_vehicle") or ""),
        "driver_name": driver.get("driver_name"),
        "fuel_type": data.get("fuel_type") or "Diesel",
        "litres": data.get("litres"), "amount": data.get("amount"),
        "kilometre": data.get("kilometre", ""),
        "fuel_station": data.get("fuel_station", ""),
        "payment_mode": data.get("payment_mode", "Cash"),
        "payment_status": data.get("payment_status") or "Paid",
        "force": data.get("force", False),
    }
    if not entry["vehicle_number"]:
        raise HTTPException(status_code=400,
                            detail="No vehicle assigned - contact the office")
    from routes.fuel import _create_entry
    return _create_entry(entry, {"email": driver.get("email", ""),
                                 "driver_name": driver.get("driver_name")},
                         module="DriverApp")


# ------------------------------------------------------------------ PWA

@router.get("/driver-portal/sw.js")
async def service_worker():
    """Minimal offline-aware service worker scoped to the driver app."""
    js = """
const CACHE = 'tsr-driver-v1';
const SHELL = ['/driver-portal', '/static/css/style.css', '/static/js/app.js'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (req.url.includes('/api/')) {
    e.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match('/driver-portal')))
  );
});
"""
    return Response(content=js, media_type="application/javascript",
                    headers={"Service-Worker-Allowed": "/driver-portal"})
