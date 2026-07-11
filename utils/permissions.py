"""Permission helpers used as FastAPI dependencies and inside routes."""
from fastapi import HTTPException, Request


def current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_admin(request: Request):
    user = require_login(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_admin_or_viewer(request: Request):
    user = require_login(request)
    if user.get("role") not in ("admin", "viewer"):
        raise HTTPException(status_code=403, detail="Not allowed")
    return user


def require_driver(request: Request):
    user = require_login(request)
    if user.get("role") != "driver":
        raise HTTPException(status_code=403, detail="Driver access required")
    return user
