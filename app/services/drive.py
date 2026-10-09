"""Read-only image downloads from client folders in Google Shared Drives."""
from __future__ import annotations

import io
import logging
from functools import lru_cache

import google_auth_httplib2
import httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

# httplib2 (what googleapiclient uses under the hood) has no default socket
# timeout, so a slow or unreachable Drive API can otherwise hang a request
# indefinitely — this is a best-effort feature (see get_drive_info/count_images),
# never worth blocking the rest of the page on.
_REQUEST_TIMEOUT_SECONDS = 10


@lru_cache
def _drive_credentials():
    # Parsing the service account file is the expensive part of building a
    # client — cache it per-process so every Drive call doesn't re-read and
    # re-parse the same JSON key file from disk.
    path = get_settings().google_service_account_file
    if not path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is required for Drive access")
    return service_account.Credentials.from_service_account_file(
        path, scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


def _is_readable_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).load()
        return True
    except Exception:
        return False


def _drive_client():
    # A fresh httplib2.Http() per call keeps this safe to use from
    # FastAPI's threadpool — googleapiclient Resource/http objects aren't
    # documented as thread-safe, only the cached credentials are shared.
    http = google_auth_httplib2.AuthorizedHttp(
        _drive_credentials(), http=httplib2.Http(timeout=_REQUEST_TIMEOUT_SECONDS)
    )
    return build("drive", "v3", http=http, cache_discovery=False)


def _image_files(drive_folder_id: str):
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
    return files, sorted(metadata, key=lambda item: (item["name"], item["id"]))


def list_image_metadata(drive_folder_id: str) -> list[tuple[str, str]]:
    """Return image IDs and names without downloading file contents."""
    _, metadata = _image_files(drive_folder_id)
    return [(item["id"], item["name"]) for item in metadata]


def download_images(images: list[tuple[str, str]], limit: int | None = None
                    ) -> list[tuple[str, str, bytes]]:
    """Download readable images in the requested order, stopping at ``limit``."""
    files = _drive_client().files()
    downloaded = []
    for file_id, file_name in images:
        data = files.get_media(fileId=file_id, supportsAllDrives=True).execute()
        if not _is_readable_image(data):
            logger.warning("Skipping unreadable Drive image %s (%s)", file_id, file_name)
            continue
        downloaded.append((file_id, file_name, data))
        if limit is not None and len(downloaded) >= limit:
            break
    return downloaded


def list_images(drive_folder_id: str) -> list[tuple[str, str, bytes]]:
    """Return every readable image directly inside the folder, in stable name/ID order.

    Files that fail to decode (corrupt, unsupported format like HEIC, etc.)
    are skipped with a warning instead of aborting the whole client's batch.
    """
    return download_images(list_image_metadata(drive_folder_id))


def count_images(drive_folder_id: str) -> int:
    """Metadata-only count of images in the folder — no bytes downloaded."""
    _, metadata = _image_files(drive_folder_id)
    return len(metadata)


def first_image(drive_folder_id: str) -> tuple[str, str, bytes] | None:
    """Return the first readable image, downloading bytes only until one decodes."""
    files, metadata = _image_files(drive_folder_id)
    for item in metadata:
        data = files.get_media(fileId=item["id"], supportsAllDrives=True).execute()
        if not _is_readable_image(data):
            logger.warning("Skipping unreadable Drive image %s (%s)", item["id"], item["name"])
            continue
        return item["id"], item["name"], data
    return None
