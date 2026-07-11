"""Authentication helpers: allowed emails, roles, driver lookup, OTP login."""
import random
import time

import httpx

from config import settings
from services import sheets_service

# in-memory OTP store: mobile -> (otp, expiry_epoch)
_otp_store = {}
OTP_TTL_SECONDS = 300


# ------------------------------------------------------------------ app users

def get_user_by_email(email):
    email = (email or "").strip().lower()
    for user in sheets_service.get_all_records("users"):
        if (user.get("email") or "").strip().lower() == email:
            return user
    return None


def resolve_role(email):
    """Return role for a Google login email, bootstrapping allowed emails as admin."""
    email = (email or "").strip().lower()
    user = get_user_by_email(email)
    if user:
        if (user.get("status") or "").lower() != "active":
            return None
        return (user.get("role") or "viewer").lower()
    if email in settings.ALLOWED_EMAILS:
        sheets_service.add_record("users", {
            "email": email, "name": email.split("@")[0].title(),
            "role": "admin", "status": "Active",
        })
        return "admin"
    return None


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def is_viewer(user):
    return bool(user) and user.get("role") == "viewer"


def is_driver(user):
    return bool(user) and user.get("role") == "driver"


# ------------------------------------------------------------------- drivers

def find_driver_by_mobile(mobile):
    mobile = "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]
    if len(mobile) != 10:
        return None
    for driver in sheets_service.get_all_records("drivers"):
        stored = "".join(ch for ch in str(driver.get("mobile_number") or "") if ch.isdigit())[-10:]
        if stored == mobile and (driver.get("status") or "").lower() == "active":
            return driver
    return None


def find_driver_by_email(email):
    email = (email or "").strip().lower()
    for driver in sheets_service.get_all_records("drivers"):
        if (driver.get("email") or "").strip().lower() == email:
            return driver
    return None


# ---------------------------------------------------------------------- OTP

def send_otp(mobile):
    """Generate an OTP for a registered driver. Returns (ok, message, dev_otp)."""
    driver = find_driver_by_mobile(mobile)
    if not driver:
        return False, "Mobile number is not registered as an active driver", None
    otp = f"{random.randint(100000, 999999)}"
    _otp_store[_clean(mobile)] = (otp, time.time() + OTP_TTL_SECONDS)
    if settings.FAST2SMS_API_KEY:
        try:
            httpx.get(
                "https://www.fast2sms.com/dev/bulkV2",
                params={"variables_values": otp, "route": "otp", "numbers": _clean(mobile)},
                headers={"authorization": settings.FAST2SMS_API_KEY},
                timeout=15,
            )
            return True, "OTP sent to your mobile number", None
        except Exception:
            return False, "Could not send OTP SMS, try again", None
    # development mode: no SMS gateway configured, surface the OTP
    return True, "OTP generated (dev mode - no SMS key configured)", otp


def verify_otp(mobile, otp):
    """Verify OTP and return the driver record on success."""
    entry = _otp_store.get(_clean(mobile))
    if not entry:
        return None
    stored_otp, expiry = entry
    if time.time() > expiry or str(otp).strip() != stored_otp:
        return None
    _otp_store.pop(_clean(mobile), None)
    return find_driver_by_mobile(mobile)


def _clean(mobile):
    return "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]
