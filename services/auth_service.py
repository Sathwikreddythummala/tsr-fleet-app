"""Authentication helpers: allowed emails, roles, employee lookup."""
from config import settings
from services import sheets_service as ss

ROLES = ("admin", "editor", "viewer", "driver")


def get_user_by_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    for user in ss.get_all_records("users"):
        if (user.get("email") or "").strip().lower() == email:
            return user
    return None


def resolve_role(email):
    """Role for a Google login, bootstrapping ALLOWED_EMAILS as admin."""
    email = (email or "").strip().lower()
    if not email:
        return None
    if email in settings.ALLOWED_EMAILS:
        user = get_user_by_email(email)
        if not user:
            ss.add_record("users", {
                "email": email, "name": email.split("@")[0].title(),
                "role": "admin", "status": "Active",
            })
        elif (user.get("role") or "") != "admin":
            ss.update_record("users", user["user_id"], {"role": "admin",
                                                        "status": "Active"})
        return "admin"

    user = get_user_by_email(email)
    if user:
        if (user.get("status") or "Active").lower() != "active":
            return None
        role = (user.get("role") or "viewer").lower()
        return role if role in ROLES else "viewer"

    driver = find_driver_by_email(email)
    if driver and (driver.get("status") or "Active") == "Active":
        return "driver"
    return None


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def can_edit(user):
    return bool(user) and user.get("role") in ("admin", "editor")


def is_viewer(user):
    return bool(user) and user.get("role") == "viewer"


def clean_mobile(mobile):
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def find_driver_by_mobile(mobile):
    target = clean_mobile(mobile)
    if not target:
        return None
    for driver in ss.get_all_records("drivers"):
        if (clean_mobile(driver.get("mobile_number")) == target
                and (driver.get("status") or "Active") == "Active"):
            return driver
    return None


def find_driver_by_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    for driver in ss.get_all_records("drivers"):
        if (driver.get("email") or "").strip().lower() == email:
            return driver
    return None
