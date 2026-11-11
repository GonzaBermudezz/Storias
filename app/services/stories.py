from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timezone

import requests

from app.engine import content
from app.services.content_jobs import build_content_config
from app.services.errors import ServiceError
from app.services.tenancy import client_or_error

logger = logging.getLogger(__name__)

# Stories in these states already happened (or are mid-flight) on Instagram:
# editing or cancelling them would silently diverge the DB from what's live.
LOCKED_STATES = {"generando", "publicando", "publicado", "cancelada"}
MAX_LEARNINGS = 8


def list_week(db, client_id: str, agency_id: str) -> list[dict]:
    client_or_error(db, client_id, agency_id)
    groups = db.table("story_groups").select("*,stories(*)").eq("client_id", client_id).gte(
        "scheduled_date", date.today().isoformat()
    ).order("scheduled_date").execute().data or []
    for group in groups:
        group["stories"] = [
            story for story in group.get("stories") or []
            if story.get("estado") not in {"cancelada", "generando"}
        ]
    return groups


def update_story_text(db, story_id: str, agency_id: str, texto_nuevo: str) -> dict:
    """Re-compose the story image with a new text and keep the correction for learning."""
    result = db.table("stories").select(
        "id,client_id,order,text,image_url,image_original_url,estado,font_choice"
    ).eq("id", story_id).maybe_single().execute()
    # postgrest-py's maybe_single() returns None outright (not a response with
    # data=None) when zero rows match — never chain `.data` directly onto it.
    story = result.data if result else None
    if not story:
        raise ServiceError(status_code=404, detail="Historia no encontrada")
    client = client_or_error(db, story["client_id"], agency_id)
    if story.get("estado") in LOCKED_STATES:
        raise ServiceError(status_code=409, detail="La historia ya fue publicada o cancelada y no se puede editar")
    if not story.get("image_original_url"):
        raise ServiceError(status_code=422, detail="La historia no tiene imagen original")
    response = requests.get(story["image_original_url"], timeout=30)
    response.raise_for_status()
    imagen_editada = content.editar_historia(
        build_content_config(client), response.content, texto_nuevo, story["order"],
        font_choice=story.get("font_choice"),
    )
    updated = db.table("stories").update({
        "text": texto_nuevo, "image_url": imagen_editada.url,
    }).eq("id", story_id).execute().data
    remember_correction(db, client, story.get("text"), texto_nuevo)
    return updated[0] if isinstance(updated, list) and updated else {
        **story, "text": texto_nuevo, "image_url": imagen_editada.url,
    }


def normalized(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def learned(client: dict) -> list[dict]:
    return [item for item in (client.get("learned_edits") or []) if isinstance(item, dict)]


def remember_correction(db, client: dict, before, after) -> None:
    """When a person changes a story's text, keep {antes, despues} (newest first, last 8)
    so the engine writes that way next time. Best-effort: never breaks the save."""
    if not str(before or "").strip() or normalized(before) == normalized(after):
        return
    item = {"id": secrets.token_hex(4), "antes": str(before).strip()[:300], "despues": str(after).strip()[:300],
            "fecha": datetime.now(timezone.utc).isoformat()}
    kept = [old for old in learned(client) if normalized(old.get("antes")) != normalized(before)]
    try:
        db.table("clients").update({"learned_edits": [item] + kept[:MAX_LEARNINGS - 1]}).eq("id", client["id"]).execute()
    except Exception:
        logger.warning("Could not remember the correction for client %s", client.get("id"), exc_info=True)
