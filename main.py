"""TSR Enterprises - Fleet & Finance Management (FastAPI entry point)."""
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from routes import (access, attendance, auth, bank, billing, company, dashboard,
                    documents, driver_portal, drivers, emi, expenses, fuel, gst,
                    house, income, mithi, outside, purse, reports, vehicles,
                    vendors)

app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None)

# paths served without an office session
PUBLIC_PREFIXES = ("/login", "/auth/", "/static/", "/health", "/unauthorized",
                   "/favicon", "/company/logo", "/.well-known/")
# the driver app runs on its own session and never touches office routes
DRIVER_PREFIXES = ("/driver-portal", "/api/driver-portal")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
# viewers may read everything but change nothing
READ_ONLY_MESSAGE = "Viewer access is read-only"


class RoleMiddleware(BaseHTTPMiddleware):
    """Session and role enforcement for every request."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path.startswith(PUBLIC_PREFIXES) or path.startswith(DRIVER_PREFIXES):
            return await call_next(request)

        user = request.session.get("user")
        if not user:
            is_api = path.startswith("/api/")
            # a signed-in driver landing on an office URL goes back to their app;
            # API calls get a JSON refusal so fetch() never receives a login page
            if request.session.get("portal_driver"):
                if is_api:
                    return JSONResponse({"detail": "Driver accounts cannot use this"},
                                        status_code=403)
                return RedirectResponse("/driver-portal")
            if is_api:
                return JSONResponse({"detail": "Login required"}, status_code=401)
            return RedirectResponse("/login")

        role = user.get("role")
        if role == "driver":
            return RedirectResponse("/driver-portal")
        if role == "viewer" and request.method in WRITE_METHODS:
            return JSONResponse({"detail": READ_ONLY_MESSAGE}, status_code=403)
        return await call_next(request)


app.add_middleware(RoleMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY,
                   max_age=settings.SESSION_MAX_AGE, same_site="lax")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    from services import sheets_service
    try:
        sheets_service.init_tables()
        print("PostgreSQL tables ready")
    except Exception as exc:
        print(f"WARNING: could not initialize database tables: {exc}")


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/company/logo")
async def company_logo():
    """Brand logo used by the sidebar, login page and favicon."""
    return company.logo_response()


@app.get("/favicon.ico")
async def favicon():
    return company.logo_response()


@app.get("/.well-known/assetlinks.json")
async def asset_links():
    """Digital Asset Links for the Android driver app (TWA)."""
    fingerprint = settings.ANDROID_CERT_FINGERPRINT
    if not fingerprint:
        return JSONResponse([])
    return JSONResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": settings.ANDROID_PACKAGE_NAME,
            "sha256_cert_fingerprints": [fingerprint],
        },
    }])


for module in (auth, dashboard, vehicles, drivers, attendance, expenses, house,
               fuel, vendors, billing, income, purse, bank, emi, gst, mithi,
               outside, reports, documents, company, access, driver_portal):
    app.include_router(module.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
