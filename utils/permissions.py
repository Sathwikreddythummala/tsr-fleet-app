"""Permission helpers used as FastAPI dependencies and inside routes.

Roles: admin (everything), editor (everything except Access), viewer
(read-only), driver (driver portal only).
"""
from fastapi import HTTPException, Request

WRITE_ROLES = ("admin", "editor")
READ_ROLES = ("admin", "editor", "viewer")


def current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_admin(request: Request):
    """Admin only - used by the Access page and other owner-level actions."""
    user = require_login(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_editor(request: Request):
    """Anyone who may change data (admin or editor)."""
    user = require_login(request)
    if user.get("role") not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="You do not have edit access")
    return user


def require_admin_or_viewer(request: Request):
    """Anyone who may read office data."""
    user = require_login(request)
    if user.get("role") not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed")
    return user


require_view = require_admin_or_viewer


def require_driver(request: Request):
    """Driver portal session (mobile + PIN), independent of Google login."""
    driver = request.session.get("portal_driver")
    if not driver:
        raise HTTPException(status_code=401, detail="Driver login required")
    return driver
