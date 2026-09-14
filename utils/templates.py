"""Jinja2 configuration and a render helper that injects shared context."""
from fastapi.templating import Jinja2Templates

from config import settings

templates = Jinja2Templates(directory="templates")


def render(request, name, context=None):
    """Render a template with user, branding and role flags already supplied."""
    from routes.company import get_profile

    try:
        profile = get_profile()
    except Exception:
        profile = {"company_name": settings.APP_NAME,
                   "company_subtitle": settings.APP_SUBTITLE}

    user = request.session.get("user")
    ctx = dict(context or {})
    ctx.update({
        "user": user,
        "role": (user or {}).get("role", ""),
        "can_edit": (user or {}).get("role") in ("admin", "editor"),
        "is_admin": (user or {}).get("role") == "admin",
        "portal_driver": request.session.get("portal_driver"),
        "app_name": profile.get("company_name") or settings.APP_NAME,
        "app_subtitle": profile.get("company_subtitle") or settings.APP_SUBTITLE,
        "company": profile,
    })
    return templates.TemplateResponse(request, name, ctx)
