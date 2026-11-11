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
import re
from functools import lru_cache

import google_auth_httplib2
import httplib2
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from PIL import Image, ImageOps

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
    return build("drive", "v3", http=_authorized_http(refresh_token), cache_discovery=False)


def _authorized_http(refresh_token: str | None = None):
    credentials = _oauth_credentials(refresh_token) if refresh_token else _service_account_credentials()
    return google_auth_httplib2.AuthorizedHttp(
        credentials, http=httplib2.Http(timeout=_REQUEST_TIMEOUT_SECONDS)
    )


_FOLDER_MIME = "application/vnd.google-apps.folder"
_MAX_SUBFOLDERS = 50


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _list_all(files, query: str) -> list[dict]:
    items, token = [], None
    while True:
        page = files.list(
            q=query, fields="nextPageToken,files(id,name)", pageSize=1000, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items.extend(page.get("files", []))
        token = page.get("nextPageToken")
        if not token:
            return items


def _image_files(drive_folder_id: str, refresh_token: str | None = None):
    if not drive_folder_id:
        raise ValueError("drive_folder_id is required")
    files = _drive_client(refresh_token).files()

    def images_in(folder_id: str) -> list[dict]:
        return _list_all(
            files, f"'{_escape_query(folder_id)}' in parents and trashed = false and mimeType contains 'image/'")

    metadata = images_in(drive_folder_id)
    # Un nivel de subcarpetas: el nombre de la subcarpeta viaja en el nombre de la
    # foto ("Osmo Pocket/IMG_1.jpg") y sirve para saber de qué producto es.
    folders = _list_all(
        files, f"'{_escape_query(drive_folder_id)}' in parents and trashed = false and mimeType = '{_FOLDER_MIME}'")
    for folder in sorted(folders, key=lambda item: (item["name"], item["id"]))[:_MAX_SUBFOLDERS]:
        try:
            for item in images_in(folder["id"]):
                metadata.append({"id": item["id"], "name": f"{folder['name']}/{item['name']}"})
        except Exception:
            # Una subcarpeta ilegible no puede romper el resto del pool.
            logger.warning("No se pudo leer la subcarpeta %s de Drive", folder["id"], exc_info=True)
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


def _as_jpeg(data: bytes, size: int) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img.thumbnail((size, size))
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
    return buffer.getvalue()


def thumbnail(file_id: str, refresh_token: str | None = None, size: int = 360) -> bytes:
    """Small JPEG of one Drive image for the portal's Fotos section (fase 3).

    Drive's own thumbnail is tried first (a few KB, no full download). Its link
    needs the same credentials, so the browser can't load it directly. If there
    is none (or it fails), the original is downloaded and shrunk with Pillow."""
    http = _authorized_http(refresh_token)
    files = build("drive", "v3", http=http, cache_discovery=False).files()
    try:
        link = files.get(fileId=file_id, fields="thumbnailLink", supportsAllDrives=True).execute().get("thumbnailLink")
        if link:
            response, content = http.request(re.sub(r"=s\d+$", f"=s{size}", link))
            if getattr(response, "status", 0) == 200 and _is_readable_image(content):
                return _as_jpeg(content, size)
    except Exception:
        logger.warning("Drive thumbnail failed for %s; downloading the original", file_id, exc_info=True)
    return _as_jpeg(files.get_media(fileId=file_id, supportsAllDrives=True).execute(), size)
