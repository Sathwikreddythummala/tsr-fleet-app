"""Company profile: branding (name + logo) and business details.

The company name and logo are read app-wide, so one deployment can be
re-branded without touching code.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from services import sheets_service as ss
from services.db import execute, fetch_one
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["company"])

FIELDS = ("company_name", "company_subtitle", "gst_number", "pan_number",
          "address", "city", "state", "pincode", "phone", "email", "website",
          "bank_name", "bank_account", "bank_ifsc", "invoice_prefix",
          "sgst_rate", "cgst_rate", "tds_rate")

DEFAULTS = {
    "company_name": "TSR Enterprises",
    "company_subtitle": "Fleet & Finance Management",
    "sgst_rate": "9",
    "cgst_rate": "9",
    "tds_rate": "2",
    "invoice_prefix": "INV",
}


def get_profile():
    """Current company profile, falling back to defaults."""
    stored = {row.get("key"): row.get("value")
              for row in ss.get_all_records("settings")}
    return {field: (stored.get(field) or DEFAULTS.get(field, "")) for field in FIELDS}


def company_name():
    return get_profile()["company_name"]


@router.get("/company-profile")
async def company_page(request: Request, user=Depends(require_view)):
    return render(request, "company_profile.html", {"profile": get_profile()})


@router.get("/api/company/profile")
async def read_profile(user=Depends(require_view)):
    profile = get_profile()
    profile["has_logo"] = _logo_row() is not None
    return profile


@router.post("/api/company/profile")
async def save_profile(request: Request, user=Depends(require_editor)):
    data = await request.json()
    saved = 0
    for field in FIELDS:
        if field in data:
            ss.set_setting(field, str(data[field]).strip())
            saved += 1
    ss.clear_cache("settings")
    ss.add_audit_log("Update", "Company", "profile",
                     f"Updated {saved} profile field(s)", user["email"])
    return {"success": True, "saved": saved}


@router.post("/api/company/logo")
async def upload_logo(file: UploadFile = File(...), user=Depends(require_editor)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 2 MB")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Logo must be an image file")

    document_id = ss.generate_id("documents")
    execute(
        "INSERT INTO document_files (document_id, entity_type, entity_id, doc_type,"
        " file_name, file_data, mime_type, uploaded_date)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (document_id, "company", "logo", "Logo", file.filename, content,
         file.content_type, ss.now_str()))
    ss.add_record("documents", {
        "document_id": document_id, "entity_type": "company", "entity_id": "logo",
        "document_type": "Logo", "file_name": file.filename,
        "drive_url": "/company/logo", "uploaded_date": ss.now_str(),
    })
    ss.add_audit_log("Upload", "Company", document_id, f"New logo: {file.filename}",
                     user["email"])
    return {"success": True, "url": "/company/logo"}


@router.delete("/api/company/logo")
async def delete_logo(user=Depends(require_editor)):
    row = _logo_row()
    if not row:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    execute("DELETE FROM document_files WHERE entity_type = 'company'"
            " AND entity_id = 'logo'")
    for d in ss.get_all_records("documents"):
        if d.get("entity_type") == "company" and d.get("entity_id") == "logo":
            ss.delete_record("documents", d["document_id"])
    ss.add_audit_log("Delete", "Company", "logo", "Removed company logo",
                     user["email"])
    return {"success": True}


def _logo_row():
    return fetch_one(
        "SELECT file_data, mime_type FROM document_files"
        " WHERE entity_type = 'company' AND entity_id = 'logo'"
        " ORDER BY uploaded_date DESC LIMIT 1")


def logo_response():
    """Serve the latest uploaded logo, or fall back to a gold truck icon."""
    try:
        row = _logo_row()
    except Exception:
        row = None
    if row and row.get("file_data") is not None:
        return Response(content=bytes(row["file_data"]),
                        media_type=row.get("mime_type") or "image/png",
                        headers={"Cache-Control": "public, max-age=300"})
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#FFD54F'/>"
        "<stop offset='1' stop-color='#FFC107'/></linearGradient></defs>"
        "<rect width='64' height='64' rx='14' fill='url(#g)'/>"
        "<path fill='#3E2723' d='M12 38V24h22v14zm24-9h7l5 6v3h-12zM18 46a4 4 0 1 1 "
        "0-8 4 4 0 0 1 0 8zm22 0a4 4 0 1 1 0-8 4 4 0 0 1 0 8z'/></svg>"
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=300"})
