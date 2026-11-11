from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.services import content_jobs, drive
from app.services.errors import ServiceError
from app.services.tenancy import select_all

logger = logging.getLogger(__name__)

SCENE_LABELS = {"producto": "Producto", "ambiente": "Ambiente", "detalle": "Detalle",
                "exterior": "Exterior", "grafico": "Gráfico", "otro": "Otra"}


def photo_state(last_used, now: datetime) -> str:
    if last_used is None:
        return "nueva"
    used = content_jobs._parse_fecha(last_used)
    if used is None or used <= now - timedelta(weeks=content_jobs.NO_REPEAT_WEEKS):
        return "disponible"
    return "reciente"


def photos_summary(db, client: dict, refresh_token: str | None, on_listed=None) -> dict:
    """Every image in the client's Drive folder (one level of subfolders), with the
    AI's tags (product, scene, person) and how often each one was used.
    `on_listed(ids)` lets the router cache the folder listing for thumbnails."""
    client_id = client["id"]
    if not client.get("drive_folder_id"):
        return {"carpeta": False, "fotos": [], "semanas_sin_repetir": content_jobs.NO_REPEAT_WEEKS}
    try:
        available = drive.list_image_metadata(client["drive_folder_id"], refresh_token)
    except Exception as exc:
        logger.warning("Could not list Drive photos for client %s", client_id, exc_info=True)
        raise ServiceError(status_code=422, detail="No se pudo leer la carpeta de Drive. Revisá la conexión en Ajustes.") from exc
    if on_listed:
        on_listed([file_id for file_id, _ in available])
    tags = content_jobs._load_tags(db, client_id)
    history = {row["drive_file_id"]: row for row in select_all(lambda: db.table("client_images").select(
        "drive_file_id,last_used_at,times_used").eq("client_id", client_id).order("drive_file_id"))}
    now = datetime.now(timezone.utc)
    photos = []
    for file_id, name in available:
        folder, _, file_name = name.rpartition("/")
        tag = tags.get(file_id)
        used = history.get(file_id) or {}
        photos.append({
            "id": file_id, "nombre": file_name, "carpeta": folder or None,
            "analizada": tag is not None,
            "producto": (tag or {}).get("producto") or None,
            "escena": SCENE_LABELS.get((tag or {}).get("escena")) if tag else None,
            "persona": bool(tag and tag.get("persona")),
            "veces_usada": int(used.get("times_used") or 0) if used else 0,
            "ultima_vez": used.get("last_used_at"),
            "estado": photo_state(used.get("last_used_at") if used else None, now) if file_id in history else "nueva",
        })
    return {"carpeta": True, "fotos": photos, "semanas_sin_repetir": content_jobs.NO_REPEAT_WEEKS}
