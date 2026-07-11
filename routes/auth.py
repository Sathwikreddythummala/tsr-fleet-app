"""Authentication routes: Google OAuth, driver OTP login, logout."""
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config import settings
from services import auth_service, sheets_service
from utils.templates import render

router = APIRouter(tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user"):
        role = request.session["user"].get("role")
        return RedirectResponse("/driver-portal" if role == "driver" else "/dashboard")
    firebase = None
    if settings.FIREBASE_API_KEY and settings.FIREBASE_PROJECT_ID:
        firebase = {
            "apiKey": settings.FIREBASE_API_KEY,
            "authDomain": settings.FIREBASE_AUTH_DOMAIN
                          or f"{settings.FIREBASE_PROJECT_ID}.firebaseapp.com",
            "projectId": settings.FIREBASE_PROJECT_ID,
        }
    return render(request, "login.html", {"firebase": firebase})


@router.get("/unauthorized")
async def unauthorized_page(request: Request):
    return render(request, "unauthorized.html")


def _redirect_uri(request: Request):
    """Configured redirect URI, or derived from the current host (any port)."""
    if settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    return str(request.base_url).rstrip("/") + "/auth/google/callback"


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

    email = (info.get("email") or "").lower()
    role = auth_service.resolve_role(email)
    if not role:
        return RedirectResponse("/unauthorized")
    if role == "driver":
        # app users with driver role must use the driver OTP login
        return RedirectResponse("/unauthorized")

    request.session["user"] = {
        "email": email,
        "name": info.get("name") or email.split("@")[0].title(),
        "picture": info.get("picture", ""),
        "role": role,
    }
    sheets_service.add_audit_log("Login", "Auth", email, f"Google login as {role}", email)
    return RedirectResponse("/dashboard")


@router.post("/api/driver/check-mobile")
async def driver_check_mobile(request: Request):
    """Confirm a mobile belongs to an active driver before sending the OTP SMS."""
    body = await request.json()
    driver = auth_service.find_driver_by_mobile(body.get("mobile", ""))
    if not driver:
        return JSONResponse(
            {"success": False,
             "message": "Mobile number is not registered as an active driver"},
            status_code=404)
    return JSONResponse({"success": True})


@router.post("/api/driver/firebase-login")
async def driver_firebase_login(request: Request):
    """Verify a Firebase Phone Auth ID token and start a driver session."""
    body = await request.json()
    token = body.get("id_token", "")
    if not token:
        return JSONResponse({"success": False, "message": "Missing token"}, status_code=400)
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
        claims = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=settings.FIREBASE_PROJECT_ID)
    except Exception:
        claims = None
    if not claims:
        return JSONResponse({"success": False, "message": "Invalid or expired login token"},
                            status_code=401)
    phone = claims.get("phone_number", "")
    driver = auth_service.find_driver_by_mobile(phone)
    if not driver:
        return JSONResponse(
            {"success": False,
             "message": "This mobile number is not registered as an active driver"},
            status_code=403)
    request.session["user"] = {
        "email": driver.get("email", ""),
        "name": driver.get("driver_name", "Driver"),
        "mobile": driver.get("mobile_number", ""),
        "driver_id": driver.get("driver_id"),
        "picture": "",
        "role": "driver",
    }
    sheets_service.add_audit_log("Login", "Auth", driver.get("driver_id"),
                                 "Driver Firebase OTP login", driver.get("email", ""))
    return JSONResponse({"success": True, "redirect": "/driver-portal"})


@router.post("/api/driver/send-otp")
async def driver_send_otp(request: Request):
    body = await request.json()
    ok, message, dev_otp = auth_service.send_otp(body.get("mobile", ""))
    payload = {"success": ok, "message": message}
    if dev_otp:
        payload["dev_otp"] = dev_otp
    return JSONResponse(payload, status_code=200 if ok else 400)


@router.post("/api/driver/verify-otp")
async def driver_verify_otp(request: Request):
    body = await request.json()
    driver = auth_service.verify_otp(body.get("mobile", ""), body.get("otp", ""))
    if not driver:
        return JSONResponse({"success": False, "message": "Invalid or expired OTP"}, status_code=400)
    request.session["user"] = {
        "email": driver.get("email", ""),
        "name": driver.get("driver_name", "Driver"),
        "mobile": driver.get("mobile_number", ""),
        "driver_id": driver.get("driver_id"),
        "picture": "",
        "role": "driver",
    }
    sheets_service.add_audit_log("Login", "Auth", driver.get("driver_id"),
                                 "Driver OTP login", driver.get("email", ""))
    return JSONResponse({"success": True, "redirect": "/driver-portal"})


@router.get("/logout")
async def logout(request: Request):
    user = request.session.get("user") or {}
    if user:
        sheets_service.add_audit_log("Logout", "Auth", user.get("email", ""),
                                     "User logged out", user.get("email", ""))
    request.session.clear()
    return RedirectResponse("/login")
