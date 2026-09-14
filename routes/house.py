"""House Expenses: a dedicated view over the household side of the Expenses table.

Rows are ordinary Expenses with category 'House Expense', so they still flow
into the main expense ledger, dashboards and reports.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils import duplicate_check
from utils.exports import excel_response, pdf_response
from utils.filters import filter_month, filter_multi, filter_range, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["house"])

CATEGORY = "House Expense"
SUBCATEGORIES = ["Rent", "Electricity", "Groceries", "Repairs", "Water",
                 "Family Expense", "Medical", "School/College Fees",
                 "Function Expense", "Gas/Cylinder", "Mobile/Internet",
                 "Travel", "Clothing", "Festival", "Other"]


@router.get("/house")
async def house_page(request: Request, user=Depends(require_view)):
    return render(request, "house.html", {"subcategories": SUBCATEGORIES})


@router.get("/api/house/list")
async def list_house(date_from: str = "", date_to: str = "", sub_category: str = "",
                     paid_by: str = "", search: str = "", month: str = "",
                     user=Depends(require_view)):
    rows = [e for e in ss.get_all_records("expenses") if e.get("category") == CATEGORY]
    rows = filter_month(rows, "expense_date", month)
    rows = filter_range(rows, "expense_date", date_from, date_to)
    rows = filter_multi(rows, "sub_category", sub_category)
    rows = filter_multi(rows, "paid_by", paid_by)
    rows = filter_search(rows, search, ["description", "sub_category", "paid_by"])
    rows.sort(key=lambda e: e.get("expense_date") or "", reverse=True)

    by_sub = {}
    for e in rows:
        key = e.get("sub_category") or "Other"
        by_sub[key] = by_sub.get(key, 0) + ss.to_float(e.get("amount"))
    month = ss.this_month()
    return {
        "rows": rows,
        "total": len(rows),
        "total_amount": round(sum(ss.to_float(e.get("amount")) for e in rows), 2),
        "month_amount": round(sum(ss.to_float(e.get("amount")) for e in rows
                                  if (e.get("expense_date") or "").startswith(month)), 2),
        "by_subcategory": {k: round(v, 2) for k, v in
                           sorted(by_sub.items(), key=lambda x: -x[1])},
    }


@router.get("/api/house/stats")
async def house_stats(user=Depends(require_view)):
    rows = [e for e in ss.get_all_records("expenses") if e.get("category") == CATEGORY]
    monthly = {}
    for e in rows:
        month = (e.get("expense_date") or "")[:7]
        if month:
            monthly[month] = monthly.get(month, 0) + ss.to_float(e.get("amount"))
    months = sorted(monthly)[-6:]
    return {"months": months, "monthly_amount": [round(monthly[m], 2) for m in months]}


@router.post("/api/house/add")
async def add_house(request: Request, user=Depends(require_editor)):
    data = await request.json()
    if ss.to_float(data.get("amount")) <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    data["category"] = CATEGORY
    data["expense_for"] = "House Expense"
    data.setdefault("expense_date", ss.today_str())
    data.setdefault("for_month", (data.get("expense_date") or "")[:7] or ss.this_month())
    if not data.get("force") and duplicate_check.check("expenses", data):
        raise HTTPException(status_code=409,
                            detail="A matching house expense already exists")
    data.pop("force", None)
    expense_id = ss.add_record("expenses", data)
    _sync_purse(expense_id, data)
    ss.add_audit_log("Create", "House", expense_id,
                     f"{data.get('sub_category')} ₹{data.get('amount')}", user["email"])
    return {"success": True, "id": expense_id}


@router.put("/api/house/{expense_id}")
async def update_house(expense_id: str, request: Request, user=Depends(require_editor)):
    if not ss.find_record_by_id("expenses", expense_id):
        raise HTTPException(status_code=404, detail="House expense not found")
    data = await request.json()
    data["category"] = CATEGORY
    data.pop("force", None)
    ss.update_record("expenses", expense_id, data)
    _clear_purse(expense_id)
    _sync_purse(expense_id, data)
    ss.add_audit_log("Update", "House", expense_id, "Updated house expense", user["email"])
    return {"success": True}


@router.delete("/api/house/{expense_id}")
async def delete_house(expense_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("expenses", expense_id):
        raise HTTPException(status_code=404, detail="House expense not found")
    ss.delete_record("expenses", expense_id)
    _clear_purse(expense_id)
    ss.add_audit_log("Delete", "House", expense_id, "Deleted house expense", user["email"])
    return {"success": True}


@router.get("/api/house/export/excel")
async def export_excel(date_from: str = "", date_to: str = "", sub_category: str = "",
                       paid_by: str = "", search: str = "", month: str = "",
                       user=Depends(require_view)):
    data = await list_house(date_from, date_to, sub_category, paid_by, search, month, user)
    headers = ["Date", "Month", "Sub Category", "Description", "Amount",
               "Payment Mode", "Paid By"]
    rows = [[e.get("expense_date"), e.get("for_month"), e.get("sub_category"),
             e.get("description"), ss.to_float(e.get("amount")),
             e.get("payment_mode"), e.get("paid_by")] for e in data["rows"]]
    return excel_response("House Expenses", headers, rows, "house_expenses.xlsx",
                          ["", "", "", "TOTAL", data["total_amount"], "", ""])


@router.get("/api/house/export/pdf")
async def export_pdf(date_from: str = "", date_to: str = "", sub_category: str = "",
                     paid_by: str = "", search: str = "", month: str = "",
                     user=Depends(require_view)):
    data = await list_house(date_from, date_to, sub_category, paid_by, search, month, user)
    headers = ["Date", "Sub Category", "Description", "Amount", "Mode", "Paid By"]
    rows = [[e.get("expense_date"), e.get("sub_category"),
             (e.get("description") or "")[:45], f"{ss.to_float(e.get('amount')):,.2f}",
             e.get("payment_mode"), e.get("paid_by")] for e in data["rows"]]
    return pdf_response("TSR Enterprises - House Expenses", headers, rows,
                        "house_expenses.pdf",
                        ["", "", "TOTAL", f"{data['total_amount']:,.2f}", "", ""])


def _sync_purse(expense_id, data):
    if (data.get("paid_by") or "").upper() in ("TSR", "TRR"):
        ss.add_record("purse", {
            "date": data.get("expense_date") or ss.today_str(),
            "holder": data["paid_by"].upper(), "type": "Expense",
            "amount": data.get("amount"),
            "description": f"House - {data.get('sub_category') or ''}",
            "category": CATEGORY, "reference_id": expense_id,
        })


def _clear_purse(expense_id):
    for p in ss.get_all_records("purse"):
        if p.get("reference_id") == expense_id:
            ss.delete_record("purse", p["purse_id"])
