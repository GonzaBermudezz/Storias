"""API of the client's own panel (/app). a81.

Isolation rule: the client_id ALWAYS comes from the session (ClientDep), never
from the URL or the body, and every query filters by it. The database is used
with the service role (RLS is bypassed), so this filter is the only thing that
keeps one client out of another's data. Anything that isn't the session
client's answers 404 with the same detail as something that doesn't exist.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.db.supabase import get_admin_client
from app.deps import ClientDep
from app.engine.formatos import FORMATOS
from app.routers.portal import _LOCKED_STATES
from app.services.content_jobs import TIMEZONE

router = APIRouter(prefix="/app/api", tags=["cliente"])

_NOT_FOUND = "No encontrado"
_HIDDEN_STATES = {"cancelada", "generando"}
_FOCUS_MAX = 200


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=_NOT_FOUND)


def _today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _client_row(db, client_id: str, fields: str) -> dict:
    result = db.table("clients").select(fields).eq("id", client_id).maybe_single().execute()
    row = result.data if result else None
    if not row:
        raise _not_found()
    return row


def _formats() -> list[dict]:
    return [{"key": key, "label": f["label"], "hint": f["hint"]} for key, f in FORMATOS.items()]


def _hhmm(value) -> str | None:
    return str(value)[:5] if value else None


class ApprovalBody(BaseModel):
    aprobado: bool


class WeekPatch(BaseModel):
    enfoque: str | None = None
    formato: str | None = None

    @field_validator("enfoque")
    @classmethod
    def clean_focus(cls, value):
        if value is None:
            return None
        value = value.strip()
        if len(value) > _FOCUS_MAX:
            raise ValueError(f"El texto puede tener hasta {_FOCUS_MAX} caracteres.")
        return value or None

    @field_validator("formato")
    @classmethod
    def known_format(cls, value):
        # Same criterion and message as the console's ClientFormatPatch.
        if value is not None and value not in FORMATOS:
            raise ValueError("Formato desconocido.")
        return value


@router.get("/me")
def get_me(client: ClientDep):
    db = get_admin_client()
    row = _client_row(db, client.client_id, "id,name,avatar_url,logo_url")
    return {
        "id": client.id, "name": client.name, "email": client.email, "role": client.role,
        "client": {"id": row["id"], "name": row.get("name"),
                   "avatar_url": row.get("avatar_url"), "logo_url": row.get("logo_url")},
    }


@router.get("/semana")
def get_week(client: ClientDep):
    db = get_admin_client()
    row = _client_row(db, client.client_id, "id,name,avatar_url,logo_url,weekly_focus,weekly_format")
    stories = db.table("stories").select(
        "id,text,image_url,order,estado,aprobado,fecha_publicacion,hora_publicacion"
    ).eq("client_id", client.client_id).gte(
        "fecha_publicacion", _today().isoformat()
    ).order("fecha_publicacion").order("order").execute().data or []

    days: dict[str, dict] = {}
    for story in stories:
        if story.get("estado") in _HIDDEN_STATES or not story.get("fecha_publicacion"):
            continue
        fecha = str(story["fecha_publicacion"])[:10]
        day = days.setdefault(fecha, {"fecha": fecha, "hora": None, "aprobado": True, "historias": []})
        day["hora"] = day["hora"] or _hhmm(story.get("hora_publicacion"))
        approved = bool(story.get("aprobado"))
        if story.get("estado") not in _LOCKED_STATES and not approved:
            day["aprobado"] = False
        day["historias"].append({
            "id": story["id"], "orden": story.get("order"), "texto": story.get("text") or "",
            "imagen": story.get("image_url"), "estado": story.get("estado"), "aprobado": approved,
        })
    for day in days.values():
        day["historias"].sort(key=lambda h: (h["orden"] is None, h["orden"] or 0))
    return {
        "cliente": {"nombre": row.get("name"), "avatar_url": row.get("avatar_url"), "logo_url": row.get("logo_url")},
        "enfoque": row.get("weekly_focus"),
        "formato": row.get("weekly_format") if row.get("weekly_format") in FORMATOS else None,
        "formatos": _formats(),
        "dias": [days[key] for key in sorted(days)],
    }


@router.patch("/historias/{story_id}/aprobar")
def approve_story(story_id: str, body: ApprovalBody, client: ClientDep):
    db = get_admin_client()
    result = db.table("stories").select("id,estado").eq("id", story_id).eq(
        "client_id", client.client_id
    ).maybe_single().execute()
    story = result.data if result else None
    if not story:
        raise _not_found()
    if story.get("estado") in _LOCKED_STATES:
        raise HTTPException(status_code=409, detail="Esta historia ya se publicó y no se puede cambiar.")
    db.table("stories").update({"aprobado": body.aprobado}).eq("id", story_id).eq(
        "client_id", client.client_id
    ).execute()
    return {"id": story_id, "aprobado": body.aprobado}


@router.patch("/dias/{fecha}/aprobar")
def approve_day(fecha: date, body: ApprovalBody, client: ClientDep):
    db = get_admin_client()
    stories = db.table("stories").select("id,estado").eq("client_id", client.client_id).eq(
        "fecha_publicacion", fecha.isoformat()
    ).execute().data or []
    ids = [row["id"] for row in stories
           if row.get("estado") not in _LOCKED_STATES and row.get("estado") not in _HIDDEN_STATES]
    if not ids:
        raise _not_found()
    db.table("stories").update({"aprobado": body.aprobado}).in_("id", ids).eq(
        "client_id", client.client_id
    ).execute()
    return {"fecha": fecha.isoformat(), "aprobado": body.aprobado, "cantidad": len(ids)}


@router.patch("/semana")
def patch_week(body: WeekPatch, client: ClientDep):
    db = get_admin_client()
    changes = {}
    if "enfoque" in body.model_fields_set:
        changes["weekly_focus"] = body.enfoque
    if "formato" in body.model_fields_set:
        changes["weekly_format"] = body.formato
    if changes:
        updated = db.table("clients").update(changes).eq("id", client.client_id).execute().data
        if not updated:
            raise _not_found()
        row = updated[0]
    else:
        row = _client_row(db, client.client_id, "id,weekly_focus,weekly_format")
    return {"enfoque": row.get("weekly_focus"), "formato": row.get("weekly_format")}
