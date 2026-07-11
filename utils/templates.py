"""Jinja2 template configuration and a render helper that injects common context."""
from fastapi.templating import Jinja2Templates

from config import settings

templates = Jinja2Templates(directory="templates")


def render(request, name, context=None):
    ctx = dict(context or {})
    ctx.update({
        "user": request.session.get("user"),
        "app_name": settings.APP_NAME,
        "app_subtitle": settings.APP_SUBTITLE,
    })
    return templates.TemplateResponse(request, name, ctx)
