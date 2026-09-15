"""Read-only image downloads from client folders in Google Shared Drives."""
from __future__ import annotations

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import get_settings


def _drive_client():
    path = get_settings().google_service_account_file
    if not path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is required for Drive access")
    credentials = service_account.Credentials.from_service_account_file(
        path, scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_images(drive_folder_id: str) -> list[tuple[str, str, bytes]]:
    """Return every image directly inside the folder, in stable name/ID order."""
    if not drive_folder_id:
        raise ValueError("drive_folder_id is required")
    folder = drive_folder_id.replace("\\", "\\\\").replace("'", "\\'")
    files = _drive_client().files()
    metadata = []
    token = None
    while True:
        page = files.list(
            q=f"'{folder}' in parents and trashed = false and mimeType contains 'image/'",
            fields="nextPageToken,files(id,name)", pageSize=1000, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        metadata.extend(page.get("files", []))
        token = page.get("nextPageToken")
        if not token:
            break
    return [(item["id"], item["name"], files.get_media(
        fileId=item["id"], supportsAllDrives=True,
    ).execute()) for item in sorted(metadata, key=lambda item: (item["name"], item["id"]))]
