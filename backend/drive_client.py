from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

def make_drive_service(service_account_json_path: str):
    creds = service_account.Credentials.from_service_account_file(
        service_account_json_path,
        scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)

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