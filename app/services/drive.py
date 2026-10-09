"""Read-only image downloads from client folders in Google Shared Drives.

Two ways to authenticate, picked per call via the optional ``refresh_token``
param every public function below takes:
  - None (default): the shared service account (GOOGLE_SERVICE_ACCOUNT_FILE).
    Requires manually sharing the client's Drive folder with that account's
    email — the original, still-supported setup.
  - A refresh token: per-client OAuth connection (see
    app/routers/portal.py::start_drive_oauth / drive_oauth_callback). The
    employee signs in with the Google account that owns the client's Drive
    and grants read-only access — no folder-sharing step needed. Preferred
    for any newly-connected client; existing clients keep working through
    the service account until someone reconnects them.
"""
from __future__ import annotations

import io
import logging
from functools import lru_cache

import google_auth_httplib2
import httplib2
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# httplib2 (what googleapiclient uses under the hood) has no default socket
# timeout, so a slow or unreachable Drive API can otherwise hang a request
# indefinitely — this is a best-effort feature (see get_drive_info/count_images),
# never worth blocking the rest of the page on.
_REQUEST_TIMEOUT_SECONDS = 10


@lru_cache
def _service_account_credentials():
    # Parsing the service account file is the expensive part of building a
    # client — cache it per-process so every Drive call doesn't re-read and
    # re-parse the same JSON key file from disk.
    path = get_settings().google_service_account_file
    if not path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is required for Drive access")
    return service_account.Credentials.from_service_account_file(path, scopes=_DRIVE_SCOPES)


def _oauth_credentials(refresh_token: str):
    # No access token yet (token=None) — google-auth fetches one on first
    # use via refresh_token, and refreshes again by itself once it expires.
    s = get_settings()
    return OAuthCredentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.google_client_id, client_secret=s.google_client_secret,
        scopes=_DRIVE_SCOPES,
    )


def _is_readable_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).load()
        return True
    except Exception:
        return False


def _drive_client(refresh_token: str | None = None):
    # A fresh httplib2.Http() per call keeps this safe to use from
    # FastAPI's threadpool — googleapiclient Resource/http objects aren't
    # documented as thread-safe, only the underlying credentials are shared
    # (and only in the service-account case; OAuth credentials are built
    # fresh per call since there's one per CLIENT, not one for the whole process).
    credentials = _oauth_credentials(refresh_token) if refresh_token else _service_account_credentials()
    http = google_auth_httplib2.AuthorizedHttp(
        credentials, http=httplib2.Http(timeout=_REQUEST_TIMEOUT_SECONDS)
    )
    return build("drive", "v3", http=http, cache_discovery=False)


def _image_files(drive_folder_id: str, refresh_token: str | None = None):
    if not drive_folder_id:
        raise ValueError("drive_folder_id is required")
    folder = drive_folder_id.replace("\\", "\\\\").replace("'", "\\'")
    files = _drive_client(refresh_token).files()
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


def list_image_metadata(drive_folder_id: str, refresh_token: str | None = None) -> list[tuple[str, str]]:
    """Return image IDs and names without downloading file contents."""
    _, metadata = _image_files(drive_folder_id, refresh_token)
    return [(item["id"], item["name"]) for item in metadata]


def download_images(images: list[tuple[str, str]], limit: int | None = None,
                     refresh_token: str | None = None) -> list[tuple[str, str, bytes]]:
    """Download readable images in the requested order, stopping at ``limit``."""
    files = _drive_client(refresh_token).files()
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


def list_images(drive_folder_id: str, refresh_token: str | None = None) -> list[tuple[str, str, bytes]]:
    """Return every readable image directly inside the folder, in stable name/ID order.

    Files that fail to decode (corrupt, unsupported format like HEIC, etc.)
    are skipped with a warning instead of aborting the whole client's batch.
    """
    return download_images(list_image_metadata(drive_folder_id, refresh_token), refresh_token=refresh_token)


def count_images(drive_folder_id: str, refresh_token: str | None = None) -> int:
    """Metadata-only count of images in the folder — no bytes downloaded."""
    _, metadata = _image_files(drive_folder_id, refresh_token)
    return len(metadata)


def first_image(drive_folder_id: str, refresh_token: str | None = None) -> tuple[str, str, bytes] | None:
    """Return the first readable image, downloading bytes only until one decodes."""
    files, metadata = _image_files(drive_folder_id, refresh_token)
    for item in metadata:
        data = files.get_media(fileId=item["id"], supportsAllDrives=True).execute()
        if not _is_readable_image(data):
            logger.warning("Skipping unreadable Drive image %s (%s)", item["id"], item["name"])
            continue
        return item["id"], item["name"], data
    return None
