"""Google Drive document storage: folder init and uploads via service account."""
import io
import json

from config import settings

FOLDERS = ("Vehicles", "Drivers", "Mithi", "Other")

_service = None
_folder_ids = {}


def _get_service():
    global _service
    if _service is not None:
        return _service
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/drive"]
    if settings.GOOGLE_CREDENTIALS_JSON:
        info = json.loads(settings.GOOGLE_CREDENTIALS_JSON)
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = service_account.Credentials.from_service_account_file(
            settings.SERVICE_ACCOUNT_FILE, scopes=scopes)
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def init_drive_folders():
    """Ensure the module subfolders exist under the configured root folder."""
    if not settings.GOOGLE_DRIVE_FOLDER_ID:
        return
    service = _get_service()
    root = settings.GOOGLE_DRIVE_FOLDER_ID
    for name in FOLDERS:
        query = (f"name = '{name}' and '{root}' in parents "
                 "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        found = service.files().list(q=query, fields="files(id)").execute().get("files", [])
        if found:
            _folder_ids[name] = found[0]["id"]
        else:
            meta = {"name": name, "parents": [root],
                    "mimeType": "application/vnd.google-apps.folder"}
            created = service.files().create(body=meta, fields="id").execute()
            _folder_ids[name] = created["id"]


def _folder_for(entity_type):
    mapping = {"vehicle": "Vehicles", "driver": "Drivers", "mithi": "Mithi"}
    name = mapping.get((entity_type or "").lower(), "Other")
    if name not in _folder_ids:
        init_drive_folders()
    return _folder_ids.get(name, settings.GOOGLE_DRIVE_FOLDER_ID)


def upload_document(entity_type, file_name, content, mime_type="application/octet-stream"):
    """Upload file bytes to the entity's Drive folder and return the view URL."""
    from googleapiclient.http import MediaIoBaseUpload

    service = _get_service()
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
    meta = {"name": file_name, "parents": [_folder_for(entity_type)]}
    created = service.files().create(
        body=meta, media_body=media, fields="id, webViewLink").execute()
    # make the file link-viewable so documents open from the app
    try:
        service.permissions().create(
            fileId=created["id"], body={"type": "anyone", "role": "reader"}).execute()
    except Exception:
        pass
    return created.get("webViewLink", f"https://drive.google.com/file/d/{created['id']}/view")
