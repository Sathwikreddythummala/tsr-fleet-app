"""Mithi module: persons who received a main amount and pay monthly Mithi.

Mithi is NOT EMI. There is no installment system: the person pays the monthly
Mithi (main_amount x rate / 100) every month until the main amount itself is
returned, at which point the person is Closed.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from services import sheets_service as ss
from utils import duplicate_check
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["mithi"])


@router.get("/mithi")
async def mithi_page(request: Request, user=Depends(require_view)):
    return render(request, "mithi.html")


@router.get("/api/mithi/persons")
async def list_persons(status: str = "", search: str = "",
                       user=Depends(require_view)):
    payments = ss.get_all_records("mithipayments")
    everyone = [_with_stats(p, payments) for p in ss.get_all_records("mithipersons")]

    rows = everyone
    if status:
        rows = [p for p in rows if (p.get("status") or "Active") == status]
    if search:
        q = search.lower()
        rows = [p for p in rows
                if q in f"{p.get('person_name', '')} "
                        f"{p.get('mobile_number', '')}".lower()]
    rows = sorted(rows, key=lambda p: (p.get("status") or "Active",
                                       (p.get("person_name") or "").lower()))

    # the summary describes the whole book, not just the visible tab
    active = [p for p in everyone if (p.get("status") or "Active") != "Closed"]
    return {
        "rows": rows,
        "shown": len(rows),
        "active_count": len(active),
        "closed_count": len(everyone) - len(active),
        "total_main_pending": round(sum(p["main_pending"] for p in everyone), 2),
        "total_collected": round(sum(p["total_paid"] for p in everyone), 2),
        "total_mithi_pending": round(sum(p["mithi_pending"] for p in everyone), 2),
    }


@router.get("/api/mithi/persons/{person_id}")
async def get_person(person_id: str, user=Depends(require_view)):
    person = ss.find_record_by_id("mithipersons", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Mithi person not found")
    payments = [p for p in ss.get_all_records("mithipayments")
                if p.get("mithi_person_id") == person_id]
    payments.sort(key=lambda p: p.get("payment_month") or "", reverse=True)
    documents = [d for d in ss.get_all_records("documents")
                 if d.get("entity_type") == "mithi" and d.get("entity_id") == person_id]
    return {"person": _with_stats(person, payments), "payments": payments,
            "documents": documents}


@router.post("/api/mithi/persons")
async def add_person(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not (data.get("person_name") or "").strip():
        raise HTTPException(status_code=400, detail="Person name is required")
    main = ss.to_float(data.get("main_amount"))
    rate = ss.to_float(data.get("mithi_rate"))
    if main <= 0 or rate <= 0:
        raise HTTPException(status_code=400, detail="Main amount and Mithi rate are required")
    data["monthly_mithi_amount"] = round(main * rate / 100, 2)
    # the money-given date anchors everything else when it is left blank
    data.setdefault("given_date", data.get("start_date") or ss.today_str())
    data.setdefault("start_date", data["given_date"])
    if not data.get("interest_start_month"):
        given = _parse_date(data["given_date"])
        if given:
            year, month = _shift(given.year, given.month, 1)
            data["interest_start_month"] = f"{year:04d}-{month:02d}"
    data.setdefault("status", "Active")
    data.setdefault("main_amount_returned", "No")
    person_id = ss.add_record("mithipersons", data)
    ss.add_audit_log("Create", "Mithi", person_id,
                     f"Added {data['person_name']} main ₹{main} @ ₹{rate}", user["email"])
    return {"success": True, "id": person_id}


@router.put("/api/mithi/persons/{person_id}")
async def update_person(person_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    person = ss.find_record_by_id("mithipersons", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Mithi person not found")
    merged = {**person, **data}
    main = ss.to_float(merged.get("main_amount"))
    rate = ss.to_float(merged.get("mithi_rate"))
    if main > 0 and rate > 0:
        data["monthly_mithi_amount"] = round(main * rate / 100, 2)
    ss.update_record("mithipersons", person_id, data)
    ss.add_audit_log("Update", "Mithi", person_id,
                     f"Updated {person.get('person_name')}", user["email"])
    return {"success": True}


@router.post("/api/mithi/persons/{person_id}/close")
async def close_person(person_id: str, request: Request, user=Depends(require_editor)):
    """Mark the main amount as returned and close the Mithi person."""
    data = await request.json()
    person = ss.find_record_by_id("mithipersons", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Mithi person not found")
    ss.update_record("mithipersons", person_id, {
        "main_amount_returned": "Yes",
        "returned_date": data.get("returned_date") or ss.today_str(),
        "status": "Closed",
    })
    ss.add_audit_log("Mithi closed", "Mithi", person_id,
                     f"Main amount returned by {person.get('person_name')}", user["email"])
    return {"success": True}


@router.delete("/api/mithi/persons/{person_id}")
async def delete_person(person_id: str, user=Depends(require_editor)):
    person = ss.find_record_by_id("mithipersons", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Mithi person not found")
    for p in ss.get_all_records("mithipayments"):
        if p.get("mithi_person_id") == person_id:
            ss.delete_record("mithipayments", p["mithi_payment_id"])
    ss.delete_record("mithipersons", person_id)
    ss.add_audit_log("Delete", "Mithi", person_id,
                     f"Deleted {person.get('person_name')} and payments", user["email"])
    return {"success": True}


@router.post("/api/mithi/persons/{person_id}/documents")
async def upload_mithi_document(person_id: str, document_type: str = Form(...),
                                file: UploadFile = File(...), user=Depends(require_editor)):
    if not ss.find_record_by_id("mithipersons", person_id):
        raise HTTPException(status_code=404, detail="Mithi person not found")
    from routes.documents import store_document
    return await store_document("mithi", person_id, document_type, file, user)


# ------------------------------------------------------------------- payments

@router.post("/api/mithi/payments")
async def add_payment(request: Request, user=Depends(require_editor)):
    data = await request.json()
    person = ss.find_record_by_id("mithipersons", data.get("mithi_person_id", ""))
    if not person:
        raise HTTPException(status_code=404, detail="Mithi person not found")
    if duplicate_check.check("mithipayments", data):
        raise HTTPException(status_code=400,
                            detail="A Mithi payment for this person and month already exists")
    data["person_name"] = person.get("person_name")
    data.setdefault("payment_date", ss.today_str())
    data.setdefault("payment_month", ss.this_month())
    if ss.to_float(data.get("mithi_amount")) <= 0:
        data["mithi_amount"] = person.get("monthly_mithi_amount")
    payment_id = ss.add_record("mithipayments", data)
    ss.add_audit_log("Payment added", "Mithi", payment_id,
                     f"₹{data['mithi_amount']} from {person.get('person_name')} "
                     f"for {data['payment_month']}", user["email"])
    return {"success": True, "id": payment_id}


@router.put("/api/mithi/payments/{payment_id}")
async def update_payment(payment_id: str, request: Request, user=Depends(require_editor)):
    data = await request.json()
    if not ss.find_record_by_id("mithipayments", payment_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    if duplicate_check.check("mithipayments", data, exclude_id=payment_id):
        raise HTTPException(status_code=400,
                            detail="A Mithi payment for this person and month already exists")
    ss.update_record("mithipayments", payment_id, data)
    ss.add_audit_log("Update", "Mithi", payment_id, "Updated Mithi payment", user["email"])
    return {"success": True}


@router.delete("/api/mithi/payments/{payment_id}")
async def delete_payment(payment_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("mithipayments", payment_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    ss.delete_record("mithipayments", payment_id)
    ss.add_audit_log("Delete", "Mithi", payment_id, "Deleted Mithi payment", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ internals

def _with_stats(person, payments):
    """Attach paid/pending month stats. Main amount stays pending until returned."""
    row = dict(person)
    person_id = person.get("mithi_person_id")
    own = [p for p in payments if p.get("mithi_person_id") == person_id]
    monthly = ss.to_float(person.get("monthly_mithi_amount"))
    total_paid = sum(ss.to_float(p.get("mithi_amount")) for p in own)
    months_due = 0
    accrual = accrual_start(person)
    end = datetime.now()
    if person.get("status") == "Closed":
        end = _parse_date(person.get("returned_date")) or end
    if accrual:
        year, month = accrual
        months_due = max(0, (end.year - year) * 12 + (end.month - month) + 1)
    expected = months_due * monthly
    row.update({
        "paid_months": sorted({p.get("payment_month") for p in own if p.get("payment_month")}),
        "months_due": months_due,
        "accrual_from": f"{accrual[0]:04d}-{accrual[1]:02d}" if accrual else "",
        "total_paid": round(total_paid, 2),
        "mithi_pending": round(max(0.0, expected - total_paid), 2),
        "main_pending": 0.0 if (person.get("main_amount_returned") or "").lower() == "yes"
        else ss.to_float(person.get("main_amount")),
        "current_month_paid": ss.this_month() in
        {p.get("payment_month") for p in own},
    })
    return row


def _shift(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def accrual_start(person):
    """(year, month) of the FIRST Mithi collection.

    Mithi is collected in arrears: an amount handed over on 1 January is
    first collected on 1 February, so the month the money is given is not
    itself due. An explicitly recorded starting month wins; otherwise it
    is the month after the amount was given.
    """
    month = (person.get("interest_start_month") or "").strip()[:7]
    if len(month) == 7 and month[4] == "-":
        try:
            return int(month[:4]), int(month[5:7])
        except ValueError:
            pass
    for field in ("given_date", "start_date"):
        parsed = _parse_date(person.get(field))
        if parsed:
            return _shift(parsed.year, parsed.month, 1)
    return None


def _parse_date(value):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value or "").strip()[:10], fmt)
        except ValueError:
            continue
    return None
