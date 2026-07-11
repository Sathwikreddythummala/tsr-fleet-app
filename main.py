"""TSR Enterprises - Fleet & Finance Management (FastAPI entry point)."""
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from routes import (access, auth, billing, dashboard, driver_portal, drivers,
                    emi, expenses, fuel, gst, mithi, purse, vehicles)

app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None)

PUBLIC_PREFIXES = ("/login", "/auth/", "/static/", "/health", "/unauthorized",
                   "/api/driver/", "/favicon")
DRIVER_ALLOWED = ("/driver-portal", "/api/driver-portal", "/logout", "/static/",
                  "/unauthorized")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class RoleMiddleware(BaseHTTPMiddleware):
    """Session + role enforcement: login required, viewer read-only, driver scoped."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        user = request.session.get("user")
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Login required"}, status_code=401)
            return RedirectResponse("/login")

        role = user.get("role")
        if role == "driver" and path != "/" and not path.startswith(DRIVER_ALLOWED):
            return RedirectResponse("/driver-portal")
        if role == "viewer" and request.method in WRITE_METHODS:
            return JSONResponse({"detail": "Viewer access is read-only"}, status_code=403)
        return await call_next(request)


app.add_middleware(RoleMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY,
                   max_age=settings.SESSION_MAX_AGE, same_site="lax")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    from services import drive_service, sheets_service
    try:
        sheets_service.init_tables()
        print("PostgreSQL tables ready")
    except Exception as exc:
        print(f"WARNING: could not initialize database tables: {exc}")
    try:
        drive_service.init_drive_folders()
        print("Google Drive folders ready")
    except Exception as exc:
        print(f"WARNING: Google Drive not initialized (uploads disabled): {exc}")


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


for module in (auth, dashboard, vehicles, drivers, expenses, fuel, billing,
               purse, emi, gst, access, driver_portal, mithi):
    app.include_router(module.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
