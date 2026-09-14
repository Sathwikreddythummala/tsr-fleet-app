"""Documents library: files stored as bytes in PostgreSQL.

Uploads go to the document_files table (with a Documents index row). Google
Drive remains supported for legacy links but is no longer required.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from services import sheets_service as ss
from services.db import execute, fetch_one
from utils.filters import filter_multi, filter_search
from utils.permissions import require_editor, require_view
from utils.templates import render

router = APIRouter(tags=["documents"])

MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
ENTITY_TYPES = ("vehicle", "driver", "mithi", "vendor", "company", "fuel", "other")


@router.get("/documents")
async def documents_page(request: Request, user=Depends(require_view)):
    return render(request, "documents.html", {"entity_types": list(ENTITY_TYPES)})


@router.get("/api/documents/list")
async def list_documents(entity_type: str = "", entity_id: str = "",
                         search: str = "", user=Depends(require_view)):
    rows = ss.get_all_records("documents")
    rows = filter_multi(rows, "entity_type", entity_type)
    if entity_id:
        rows = [d for d in rows if d.get("entity_id") == entity_id]
    rows = filter_search(rows, search, ["file_name", "document_type", "entity_id"])
    rows.sort(key=lambda d: d.get("uploaded_date") or "", reverse=True)

    names = _entity_names()
    for row in rows:
        row["entity_name"] = names.get((row.get("entity_type"), row.get("entity_id")),
                                       row.get("entity_id") or "")
    return {"rows": rows, "total": len(rows)}


@router.post("/api/documents/upload")
async def upload_document(entity_type: str = Form(...), entity_id: str = Form(""),
                          document_type: str = Form(...), file: UploadFile = File(...),
                          user=Depends(require_editor)):
    return await store_document(entity_type, entity_id, document_type, file, user)


async def store_document(entity_type, entity_id, document_type, file, user):
    """Shared upload used by every module's document section."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File is larger than 10 MB")

    document_id = ss.generate_id("documents")
    execute(
        "INSERT INTO document_files (document_id, entity_type, entity_id, doc_type,"
        " file_name, file_data, mime_type, uploaded_date)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (document_id, entity_type, entity_id or "", document_type, file.filename,
         content, file.content_type or "application/octet-stream", ss.now_str()))
    ss.add_record("documents", {
        "document_id": document_id, "entity_type": entity_type,
        "entity_id": entity_id or "", "document_type": document_type,
        "file_name": file.filename, "drive_url": f"/documents/file/{document_id}",
        "uploaded_date": ss.now_str(),
    })
    ss.add_audit_log("Upload", "Documents", document_id,
                     f"{document_type}: {file.filename} ({entity_type} {entity_id})",
                     user.get("email", ""))
    return {"success": True, "id": document_id, "url": f"/documents/file/{document_id}"}


@router.get("/documents/file/{document_id}")
async def serve_document(document_id: str, download: int = 0,
                         user=Depends(require_view)):
    row = fetch_one("SELECT file_name, file_data, mime_type FROM document_files"
                    " WHERE document_id = %s", (document_id,))
    if not row or row.get("file_data") is None:
        record = ss.find_record_by_id("documents", document_id)
        if record and (record.get("drive_url") or "").startswith("http"):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(record["drive_url"])
        raise HTTPException(status_code=404, detail="File not found")
    disposition = "attachment" if download else "inline"
    return Response(
        content=bytes(row["file_data"]),
        media_type=row.get("mime_type") or "application/octet-stream",
        headers={"Content-Disposition":
                 f'{disposition}; filename="{row.get("file_name") or document_id}"'})


@router.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, user=Depends(require_editor)):
    if not ss.find_record_by_id("documents", document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    execute("DELETE FROM document_files WHERE document_id = %s", (document_id,))
    ss.delete_record("documents", document_id)
    ss.add_audit_log("Delete", "Documents", document_id, "Deleted document",
                     user["email"])
    return {"success": True}


def _entity_names():
    """Map (entity_type, id) -> readable name for the documents table."""
    names = {}
    for v in ss.get_all_records("vehicles"):
        names[("vehicle", v.get("vehicle_id"))] = v.get("vehicle_number") or ""
    for d in ss.get_all_records("drivers"):
        names[("driver", d.get("driver_id"))] = d.get("driver_name") or ""
    for m in ss.get_all_records("mithipersons"):
        names[("mithi", m.get("mithi_person_id"))] = m.get("person_name") or ""
    for ven in ss.get_all_records("vendors"):
        names[("vendor", ven.get("vendor_id"))] = ven.get("vendor_name") or ""
    return names
