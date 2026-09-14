"""Authentication: Google OAuth for office users, logout, unauthorized page.

Drivers do not use Google - they sign in with mobile + PIN in routes/driver_portal.
"""
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from config import settings
from services import auth_service, sheets_service as ss
from utils.templates import render

router = APIRouter(tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _redirect_uri(request: Request):
    """Configured redirect URI, or derived from the current host."""
    if settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    return str(request.base_url).rstrip("/") + "/auth/google/callback"


@router.get("/login")
async def login_page(request: Request):
    user = request.session.get("user")
    if user:
        return RedirectResponse("/dashboard")
    return render(request, "login.html",
                  {"google_ready": bool(settings.GOOGLE_CLIENT_ID)})


@router.get("/unauthorized")
async def unauthorized_page(request: Request):
    return render(request, "unauthorized.html")


@router.get("/auth/google")
async def google_login(request: Request):
    if not settings.GOOGLE_CLIENT_ID:
        return RedirectResponse("/unauthorized")
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = ""):
    if not code:
        return RedirectResponse("/login")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token_resp = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
            })
            token = token_resp.json()
            if "access_token" not in token:
                return RedirectResponse("/unauthorized")
            info_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token['access_token']}"})
            info = info_resp.json()
    except Exception:
        return RedirectResponse("/unauthorized")

    email = (info.get("email") or "").lower()
    role = auth_service.resolve_role(email)
    if not role or role == "driver":
        # drivers use the mobile + PIN app, not the office console
        return RedirectResponse("/unauthorized")

    request.session["user"] = {
        "email": email,
        "name": info.get("name") or email.split("@")[0].title(),
        "picture": info.get("picture", ""),
        "role": role,
    }
    ss.add_audit_log("Login", "Auth", email, f"Google login as {role}", email)
    return RedirectResponse("/dashboard")


@router.get("/logout")
async def logout(request: Request):
    user = request.session.get("user") or {}
    if user:
        ss.add_audit_log("Logout", "Auth", user.get("email", ""), "User logged out",
                         user.get("email", ""))
    request.session.pop("user", None)
    return RedirectResponse("/login")
