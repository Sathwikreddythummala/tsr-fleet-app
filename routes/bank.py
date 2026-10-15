"""Bank accounts and cash-in-hand.

Money model
-----------
Bank balance  = opening balance
              + Credit + Deposit + Transfer In
              - Debit  - Withdrawal - Transfer Out

Cash in hand  = opening cash
              + Withdrawal (cash taken out of a bank)
              - Deposit    (cash put back into a bank)
              - cash handed to a purse holder (purse Credit entries)
              - company cash expenses (payment mode Cash, nobody's purse)

An expense tagged with a bank account posts a matching Debit automatically,
the same way purse-paid expenses mirror into the purse.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.exports import excel_response
from utils.filters import filter_month, filter_multi, filter_range, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["bank"])

# money in / money out, from the account's point of view
MONEY_IN = ("Credit", "Deposit", "Transfer In", "Opening")
MONEY_OUT = ("Debit", "Withdrawal", "Transfer Out")
TXN_TYPES = ("Credit", "Debit", "Withdrawal", "Deposit", "Transfer")

CASH_OPENING_KEY = "cash_opening_balance"

# brand colours so each bank card is recognisable at a glance
BANK_COLOURS = {
    "SBI": "#22409A",
    "State Bank of India": "#22409A",
    "TGRB": "#0E7A3C",
    "Telangana Grameena Bank": "#0E7A3C",
    "South Indian Bank": "#C8102E",
    "HDFC Bank": "#004C8F",
    "ICICI Bank": "#AE282E",
    "Axis Bank": "#97144D",
    "Kotak Mahindra Bank": "#ED1C24",
    "Canara Bank": "#0B4EA2",
    "Union Bank of India": "#E31E24",
    "Bank of Baroda": "#F7941D",
    "Punjab National Bank": "#A41E34",
    "Indian Bank": "#1B3D6D",
    "Bank of India": "#F58220",
}
DEFAULT_COLOUR = "#5D4037"

SUGGESTED_BANKS = ["SBI", "TGRB", "South Indian Bank", "HDFC Bank", "ICICI Bank",
                   "Axis Bank", "Kotak Mahindra Bank", "Canara Bank",
                   "Union Bank of India", "Bank of Baroda",
                   "Punjab National Bank", "Indian Bank", "Other"]


def colour_for(bank_name, given=""):
    if (given or "").strip():
        return given.strip()
    return BANK_COLOURS.get((bank_name or "").strip(), DEFAULT_COLOUR)


@router.get("/bank")
async def bank_page(request: Request, user=Depends(require_view)):
    return render(request, "bank.html", {"suggested_banks": SUGGESTED_BANKS,
                                         "txn_types": list(TXN_TYPES)})


# ------------------------------------------------------------------ balances

def _account_balances():
    """account_id -> running balance, from opening balance plus every movement."""
    balances = {}
    for account in ss.get_all_records("bankaccounts"):
        balances[account["account_id"]] = ss.to_float(account.get("opening_balance"))
    for txn in ss.get_all_records("banktransactions"):
        amount = ss.to_float(txn.get("amount"))
        kind = txn.get("type")
        account_id = txn.get("account_id")
        if account_id in balances:
            if kind in MONEY_IN:
                balances[account_id] += amount
            elif kind in MONEY_OUT:
                balances[account_id] -= amount
        # a transfer also lands in the destination account
        if kind == "Transfer Out" and txn.get("to_account_id") in balances:
            balances[txn["to_account_id"]] += amount
    return balances


def _cash_in_hand():
    """Physical cash the company holds, outside the TSR/TRR purses."""
    opening = ss.to_float(ss.get_setting(CASH_OPENING_KEY, "0"))
    txns = ss.get_all_records("banktransactions")
    withdrawn = sum(ss.to_float(t.get("amount")) for t in txns
                    if t.get("type") == "Withdrawal")
    deposited = sum(ss.to_float(t.get("amount")) for t in txns
                    if t.get("type") == "Deposit")
    # cash handed over to a purse holder leaves the central cash pot
    to_purse = sum(ss.to_float(p.get("amount")) for p in ss.get_all_records("purse")
                   if p.get("type") == "Credit")
    # company cash expenses (a purse holder's spending is tracked in the purse)
    cash_spent = sum(ss.to_float(e.get("amount")) for e in ss.get_all_records("expenses")
                     if (e.get("payment_mode") or "") == "Cash"
                     and not (e.get("paid_by") or "").strip()
                     and not (e.get("bank_account") or "").strip())
    return {
        "opening": round(opening, 2),
        "withdrawn": round(withdrawn, 2),
        "deposited": round(deposited, 2),
        "to_purse": round(to_purse, 2),
        "cash_spent": round(cash_spent, 2),
        "balance": round(opening + withdrawn - deposited - to_purse - cash_spent, 2),
    }


@router.get("/api/bank/accounts")
async def list_accounts(status: str = "", user=Depends(require_view)):
    balances = _account_balances()
    txns = ss.get_all_records("banktransactions")
    month = ss.this_month()

    rows = filter_multi(ss.get_all_records("bankaccounts"), "status", status,
                        default="Active")
    rows.sort(key=lambda a: (a.get("bank_name") or "").lower())
    for account in rows:
        own = [t for t in txns if t.get("account_id") == account["account_id"]]
        this_month = [t for t in own if (t.get("txn_date") or "").startswith(month)]
        account["balance"] = round(balances.get(account["account_id"], 0.0), 2)
        account["colour"] = colour_for(account.get("bank_name"), account.get("colour"))
        account["txn_count"] = len(own)
        account["month_in"] = round(sum(ss.to_float(t.get("amount")) for t in this_month
                                        if t.get("type") in MONEY_IN), 2)
        account["month_out"] = round(sum(ss.to_float(t.get("amount")) for t in this_month
                                         if t.get("type") in MONEY_OUT), 2)
        account["last_txn"] = max((t.get("txn_date") or "" for t in own), default="")

    cash = _cash_in_hand()
    return {
        "rows": rows,
        "cash": cash,
        "total_bank": round(sum(a["balance"] for a in rows), 2),
        "total_with_cash": round(sum(a["balance"] for a in rows) + cash["balance"], 2),
        "account_count": len(rows),
    }


@router.post("/api/bank/accounts")
async def add_account(request: Request, user=Depends(require_editor)):
    data = await request.json()
    bank_name = (data.get("bank_name") or "").strip()
    if not bank_name:
        raise HTTPException(status_code=400, detail="Bank name is required")
    number = (data.get("account_number") or "").strip()
    if number and any((a.get("account_number") or "").strip() == number
                      for a in ss.get_all_records("bankaccounts")):
        raise HTTPException(status_code=400,
                            detail="An account with this number already exists")
    data["bank_name"] = bank_name
    data["colour"] = colour_for(bank_name, data.get("colour"))
    data["opening_balance"] = ss.to_float(data.get("opening_balance"))
    data.setdefault("opening_date", ss.today_str())
    data.setdefault("status", "Active")
    data.setdefault("account_type", "Current")
    account_id = ss.add_record("bankaccounts", data)
    ss.add_audit_log("Create", "Bank", account_id,
                     f"Added {bank_name} with opening ₹{data['opening_balance']}",
                     user["email"])
    return {"success": True, "id": account_id}


@router.put("/api/bank/accounts/{account_id}")
async def update_account(account_id: str, request: Request,
                         user=Depends(require_editor)):
    account = ss.find_record_by_id("bankaccounts", account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = await request.json()
    if data.get("bank_name"):
        data["bank_name"] = data["bank_name"].strip()
        data["colour"] = colour_for(data["bank_name"], data.get("colour"))
    if "opening_balance" in data:
        data["opening_balance"] = ss.to_float(data["opening_balance"])
    ss.update_record("bankaccounts", account_id, data)
    ss.add_audit_log("Update", "Bank", account_id,
                     f"Updated {account.get('bank_name')}", user["email"])
    return {"success": True}


@router.delete("/api/bank/accounts/{account_id}")
async def delete_account(account_id: str, user=Depends(require_editor)):
    account = ss.find_record_by_id("bankaccounts", account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    in_use = sum(1 for t in ss.get_all_records("banktransactions")
                 if t.get("account_id") == account_id)
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"{account.get('bank_name')} has {in_use} transaction(s) — "
                   "mark it Inactive instead of deleting")
    ss.delete_record("bankaccounts", account_id)
    ss.add_audit_log("Delete", "Bank", account_id,
                     f"Deleted {account.get('bank_name')}", user["email"])
    return {"success": True}


# ------------------------------------------------------------------ cash opening

@router.post("/api/bank/cash-opening")
async def set_cash_opening(request: Request, user=Depends(require_editor)):
    """One-time starting cash-in-hand figure."""
    data = await request.json()
    amount = ss.to_float(data.get("amount"))
    ss.set_setting(CASH_OPENING_KEY, amount)
    ss.add_audit_log("Update", "Bank", "cash",
                     f"Opening cash in hand set to ₹{amount}", user["email"])
    return {"success": True, "amount": amount}


# ------------------------------------------------------------------ transactions

@router.get("/api/bank/transactions")
async def list_transactions(account: str = "", txn_type: str = "", month: str = "",
                            date_from: str = "", date_to: str = "", search: str = "",
                            user=Depends(require_view)):
    rows = ss.get_all_records("banktransactions")
    rows = filter_multi(rows, "account_id", account)
    rows = filter_multi(rows, "type", txn_type)
    rows = filter_month(rows, "txn_date", month)
    rows = filter_range(rows, "txn_date", date_from, date_to)
    rows = filter_search(rows, search, ["description", "reference_number",
                                        "category", "bank_name"])
    rows.sort(key=lambda t: (t.get("txn_date") or "", t.get("created_date") or ""),
              reverse=True)
    return {
        "rows": rows,
        "total_in": round(sum(ss.to_float(t.get("amount")) for t in rows
                              if t.get("type") in MONEY_IN), 2),
        "total_out": round(sum(ss.to_float(t.get("amount")) for t in rows
                               if t.get("type") in MONEY_OUT), 2),
        "total": len(rows),
    }


@router.post("/api/bank/transactions")
async def add_transaction(request: Request, user=Depends(require_editor)):
    """Credit, Debit, Withdrawal (to cash), Deposit (from cash) or Transfer."""
    data = await request.json()
    kind = data.get("type")
    if kind not in TXN_TYPES:
        raise HTTPException(status_code=400, detail="Invalid transaction type")
    amount = ss.to_float(data.get("amount"))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    account = ss.find_record_by_id("bankaccounts", data.get("account_id", ""))
    if not account:
        raise HTTPException(status_code=404, detail="Choose a bank account")

    payload = {
        "txn_date": data.get("txn_date") or ss.today_str(),
        "account_id": account["account_id"],
        "bank_name": account.get("bank_name"),
        "amount": amount,
        "category": data.get("category", ""),
        "description": data.get("description", ""),
        "reference_number": data.get("reference_number", ""),
        "payment_mode": data.get("payment_mode", ""),
        "holder": data.get("holder", ""),
    }

    if kind == "Transfer":
        target = ss.find_record_by_id("bankaccounts", data.get("to_account_id", ""))
        if not target:
            raise HTTPException(status_code=404, detail="Choose the destination account")
        if target["account_id"] == account["account_id"]:
            raise HTTPException(status_code=400,
                                detail="Pick two different accounts for a transfer")
        payload["type"] = "Transfer Out"
        payload["to_account_id"] = target["account_id"]
        payload.setdefault("category", "Transfer")
        payload["description"] = (payload["description"]
                                  or f"Transfer to {target.get('bank_name')}")
        txn_id = ss.add_record("banktransactions", payload)
        ss.add_audit_log("Create", "Bank", txn_id,
                         f"₹{amount} {account.get('bank_name')} → "
                         f"{target.get('bank_name')}", user["email"])
        return {"success": True, "id": txn_id}

    payload["type"] = kind
    txn_id = ss.add_record("banktransactions", payload)

    # a withdrawal can go straight into a purse holder's hands
    holder = (data.get("holder") or "").strip().upper()
    if kind == "Withdrawal" and holder in ("TSR", "TRR"):
        ss.add_record("purse", {
            "date": payload["txn_date"], "holder": holder, "type": "Credit",
            "amount": amount,
            "description": f"Cash withdrawn from {account.get('bank_name')}",
            "category": "Bank Withdrawal", "reference_id": txn_id,
        })

    ss.add_audit_log("Create", "Bank", txn_id,
                     f"{kind} ₹{amount} on {account.get('bank_name')}", user["email"])
    return {"success": True, "id": txn_id}


@router.delete("/api/bank/transactions/{txn_id}")
async def delete_transaction(txn_id: str, user=Depends(require_editor)):
    txn = ss.find_record_by_id("banktransactions", txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.get("reference_id"):
        raise HTTPException(
            status_code=400,
            detail="This entry came from an expense — delete the expense instead")
    # remove the purse credit a withdrawal created
    for p in ss.get_all_records("purse"):
        if p.get("reference_id") == txn_id:
            ss.delete_record("purse", p["purse_id"])
    ss.delete_record("banktransactions", txn_id)
    ss.add_audit_log("Delete", "Bank", txn_id, "Deleted bank transaction",
                     user["email"])
    return {"success": True}


@router.get("/api/bank/summary")
async def bank_summary(user=Depends(require_view)):
    balances = _account_balances()
    cash = _cash_in_hand()
    return {"total_bank": round(sum(balances.values()), 2),
            "cash_in_hand": cash["balance"],
            "total_funds": round(sum(balances.values()) + cash["balance"], 2)}


@router.get("/api/bank/export/excel")
async def export_transactions(account: str = "", txn_type: str = "", month: str = "",
                              date_from: str = "", date_to: str = "", search: str = "",
                              user=Depends(require_view)):
    data = await list_transactions(account, txn_type, month, date_from, date_to,
                                   search, user)
    headers = ["Date", "Bank", "Type", "Category", "Description", "Reference",
               "Money In", "Money Out"]
    rows = [[t.get("txn_date"), t.get("bank_name"), t.get("type"), t.get("category"),
             t.get("description"), t.get("reference_number"),
             ss.to_float(t.get("amount")) if t.get("type") in MONEY_IN else "",
             ss.to_float(t.get("amount")) if t.get("type") in MONEY_OUT else ""]
            for t in data["rows"]]
    totals = ["", "", "", "", "", "TOTAL", data["total_in"], data["total_out"]]
    return excel_response("Bank", headers, rows, "bank_transactions.xlsx", totals)


# ------------------------------------------------------- used by other modules

def sync_expense(expense_id, data):
    """Post a Debit when an expense is paid from a bank account."""
    account_id = (data.get("bank_account") or "").strip()
    if not account_id:
        return
    account = ss.find_record_by_id("bankaccounts", account_id)
    if not account:
        return
    ss.add_record("banktransactions", {
        "txn_date": data.get("expense_date") or ss.today_str(),
        "account_id": account_id, "bank_name": account.get("bank_name"),
        "type": "Debit", "amount": ss.to_float(data.get("amount")),
        "category": data.get("category", ""),
        "description": (data.get("description")
                        or f"{data.get('category', 'Expense')}"),
        "payment_mode": data.get("payment_mode", ""),
        "reference_id": expense_id,
    })


def clear_expense(expense_id):
    """Remove the bank debit mirrored from an expense."""
    for txn in ss.get_all_records("banktransactions"):
        if txn.get("reference_id") == expense_id:
            ss.delete_record("banktransactions", txn["txn_id"])
