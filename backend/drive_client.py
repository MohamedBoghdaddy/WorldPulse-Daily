from pathlib import Path
from typing import Optional
import os, json

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

def make_drive_service(credentials_path: str | None = None):
    sa_json = os.getenv("GCP_SA_JSON", "").strip()

    if sa_json:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        if not credentials_path:
            raise RuntimeError("Missing Drive credentials: set GCP_SA_JSON or GOOGLE_APPLICATION_CREDENTIALS")
        creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)

    return build("drive", "v3", credentials=creds)

def upload_file(
    svc,
    folder_id: str,
    local_path: str,
    mime_type: str,
    name: Optional[str] = None
) -> str:
    p = Path(local_path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    file_metadata = {
        "name": name or p.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(str(p), mimetype=mime_type, resumable=True)
    created = svc.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()
    return created["id"]