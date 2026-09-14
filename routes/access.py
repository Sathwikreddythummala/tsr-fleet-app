"""Access module: admin-only user and role management."""
from fastapi import APIRouter, Depends, HTTPException, Request

from services import sheets_service as ss
from utils.permissions import require_admin
from utils.templates import render

router = APIRouter(tags=["access"])

ROLES = ("admin", "editor", "viewer", "driver")


@router.get("/access")
async def access_page(request: Request, user=Depends(require_admin)):
    return render(request, "access.html")


@router.get("/api/access/users")
async def list_users(user=Depends(require_admin)):
    return sorted(ss.get_all_records("users"), key=lambda u: u.get("email") or "")


@router.post("/api/access/users")
async def add_user(request: Request, user=Depends(require_admin)):
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if data.get("role") not in ROLES:
        raise HTTPException(status_code=400, detail="Role must be admin, viewer or driver")
    if any((u.get("email") or "").lower() == email for u in ss.get_all_records("users")):
        raise HTTPException(status_code=400, detail="User already exists")
    user_id = ss.add_record("users", {
        "email": email, "name": data.get("name", ""),
        "role": data["role"], "status": data.get("status", "Active"),
    })
    ss.add_audit_log("Create", "Access", user_id, f"Added {email} as {data['role']}",
                     user["email"])
    return {"success": True, "id": user_id}


@router.put("/api/access/users/{user_id}")
async def update_user(user_id: str, request: Request, user=Depends(require_admin)):
    data = await request.json()
    target = ss.find_record_by_id("users", user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if data.get("role") and data["role"] not in ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if (target.get("email") or "").lower() == user["email"].lower():
        if data.get("role") not in (None, "", "admin") or \
           (data.get("status") and data["status"] != "Active"):
            raise HTTPException(status_code=400,
                                detail="You cannot demote or deactivate your own account")
    ss.update_record("users", user_id, data)
    ss.add_audit_log("Update", "Access", user_id,
                     f"Updated user {target.get('email')}", user["email"])
    return {"success": True}


@router.delete("/api/access/users/{user_id}")
async def delete_user(user_id: str, user=Depends(require_admin)):
    target = ss.find_record_by_id("users", user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if (target.get("email") or "").lower() == user["email"].lower():
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    ss.delete_record("users", user_id)
    ss.add_audit_log("Delete", "Access", user_id,
                     f"Deleted user {target.get('email')}", user["email"])
    return {"success": True}


@router.get("/api/access/audit-logs")
async def audit_logs(limit: int = 100, user=Depends(require_admin)):
    rows = sorted(ss.get_all_records("auditlogs"),
                  key=lambda l: l.get("timestamp") or "", reverse=True)
    return rows[:max(1, min(limit, 500))]
