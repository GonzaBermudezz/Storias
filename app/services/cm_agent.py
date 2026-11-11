"""CM con IA: el bucle de la conversación (CLAUDE.md, sección 16).

El motor (app/engine/cm.py) hace UNA llamada a Claude por turno y no ejecuta nada.
Acá vive todo lo demás: reclamo de la conversación, persistencia de cada mensaje,
ejecución de las herramientas de lectura atadas al cliente de la conversación y los
límites. Las herramientas `proponer_*` NUNCA escriben el dato real: validan y dejan una
fila `pending` en cm_pending_actions que una persona aprueba o descarta (más abajo).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.config import get_settings
from app.engine import cm
from app.engine.exceptions import ClaudeGenerationError
from app.engine.schemas import ContextoCM
from app.engine.formatos import FORMATOS
from app.services import brand, history, photos, stories, web_text
from app.services.content_jobs import TIMEZONE, build_content_config
from app.services.encryption import decrypt
from app.services.errors import ServiceError
from app.services.tenancy import CLIENT_DETAIL, client_or_error

logger = logging.getLogger(__name__)

_CLOSING_TAG = "</contexto_actual>\n"
_TITLE_MAX = 80
_PHOTOS_CAP = 60
_CLIENT_FIELDS = CLIENT_DETAIL + ",active"
_UNRUN_TOOL = "La herramienta no llegó a ejecutarse."
_DB_UNAVAILABLE = "El CM no está disponible en este momento. Probá de nuevo."


class ToolError(Exception):
    """A tool failed in a way the model should see (is_error), not the person."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _z(moment: datetime) -> str:
    """UTC with a Z suffix: no '+' or ':' offset to be mangled inside a PostgREST filter."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(value) -> str:
    """Deterministic serialization: tool results are replayed byte-identical."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# ── Conversaciones ────────────────────────────────────────────────────────────

def _summary(row: dict) -> dict:
    return {"id": row["id"], "titulo": row.get("title"), "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"), "cerrada": row.get("closed_at") is not None,
            "turnos": int(row.get("user_turns") or 0)}


def _require_active(client: dict) -> None:
    if not client.get("active"):
        raise ServiceError(status_code=409, detail="El cliente está inactivo.")


def create_conversation(db, client_id: str, employee, titulo: str | None) -> dict:
    client = client_or_error(db, client_id, employee.agency_id, _CLIENT_FIELDS)
    _require_active(client)
    inicio = cm.iniciar_conversacion_cm(build_content_config(client))
    title = (titulo or "").strip()[:_TITLE_MAX] or None
    row = db.table("cm_conversations").insert({
        "client_id": client_id, "created_by": employee.id, "title": title,
        "system_prompt": inicio.system, "tools_version": inicio.version_herramientas,
        "user_turns": 0,
    }).execute().data[0]
    return _summary(row)


def list_conversations(db, client_id: str, agency_id: str) -> list[dict]:
    client_or_error(db, client_id, agency_id)
    rows = db.table("cm_conversations").select(
        "id,title,created_at,updated_at,closed_at,user_turns"
    ).eq("client_id", client_id).order("updated_at", desc=True).limit(50).execute().data or []
    return [_summary(row) for row in rows]


def load_conversation(db, conversation_id: str, agency_id: str) -> tuple[dict, dict]:
    """The conversation and its client. Anything outside the agency answers 404,
    the same as a conversation that doesn't exist."""
    result = db.table("cm_conversations").select("*").eq("id", conversation_id).maybe_single().execute()
    conv = result.data if result else None
    if not conv:
        raise ServiceError(status_code=404, detail="Conversación no encontrada")
    try:
        client = client_or_error(db, conv["client_id"], agency_id, _CLIENT_FIELDS)
    except ServiceError as exc:
        raise ServiceError(status_code=404, detail="Conversación no encontrada") from exc
    return conv, client


def conversation_summary(conv: dict) -> dict:
    return _summary(conv)


def update_conversation(db, conversation_id: str, agency_id: str, titulo: str | None, cerrada: bool | None) -> dict:
    conv, _ = load_conversation(db, conversation_id, agency_id)
    changes: dict = {}
    if titulo is not None:
        changes["title"] = titulo.strip()[:_TITLE_MAX] or None
    if cerrada is not None:
        changes["closed_at"] = _iso(_now()) if cerrada else None
    if changes:
        changes["updated_at"] = _iso(_now())
        conv = db.table("cm_conversations").update(changes).eq("id", conversation_id).execute().data[0]
    return _summary(conv)


def _display_text(role: str, kind: str, blocks) -> str:
    if not isinstance(blocks, list):
        return ""
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    text = "\n".join(t for t in texts if t).strip()
    if role == "user" and _CLOSING_TAG in text:
        text = text.split(_CLOSING_TAG, 1)[1].strip()
    return text


def visible_messages(db, conversation_id: str) -> list[dict]:
    """What the browser may see: plain text only. Thinking, tool calls and results,
    the per-turn context and audit-only rows never leave the server."""
    rows = db.table("cm_messages").select("id,role,kind,content,replay,created_at").eq(
        "conversation_id", conversation_id).order("seq").execute().data or []
    out = []
    for row in rows:
        if not row.get("replay") or row["kind"] == "tool_result":
            continue
        text = _display_text(row["role"], row["kind"], row.get("content"))
        if text:
            out.append({"id": row["id"], "rol": row["role"], "texto": text, "created_at": row.get("created_at")})
    return out


# ── Herramientas de lectura ───────────────────────────────────────────────────

def _compact_week(groups: list[dict]) -> list[dict]:
    week = []
    for group in groups[:8]:
        week.append({"fecha": group.get("scheduled_date"), "historias": [
            {"id": s.get("id"), "orden": s.get("order"), "estado": s.get("estado"),
             "aprobada": bool(s.get("aprobado")), "fecha": s.get("fecha_publicacion"),
             "texto": str(s.get("text") or "")[:200]}
            for s in group.get("stories") or []]})
    return week


def _tool_marca(db, conv, client, call_input, employee):
    return {key: client.get(key) for key in (
        "name", "business_description", "weekly_focus", "weekly_format", "voice_style",
        "tone_examples", "topics", "website_url")}


def _tool_semana(db, conv, client, call_input, employee):
    return _compact_week(stories.list_week(db, client["id"], employee.agency_id))


def _tool_historial(db, conv, client, call_input, employee):
    rows = history.prompt_history(db, client["id"], employee.agency_id)[:20]
    return [{"campo": r.get("field"), "antes": str(r.get("old_value") or "")[:300],
             "despues": str(r.get("new_value") or "")[:300], "fecha": r.get("changed_at"),
             "quien": r.get("changed_by_name")} for r in rows]


def _tool_fotos(db, conv, client, call_input, employee):
    # photos_summary does no tenancy check of its own: the client comes from here.
    fresh = client_or_error(db, client["id"], employee.agency_id, CLIENT_DETAIL + ",drive_refresh_token_encrypted")
    encrypted = fresh.pop("drive_refresh_token_encrypted", None)
    summary = photos.photos_summary(db, fresh, decrypt(encrypted) if encrypted else None)
    items = summary.get("fotos") or []
    if call_input.get("solo_disponibles"):
        items = [p for p in items if p.get("estado") in {"nueva", "disponible"}]
    return {"carpeta": summary.get("carpeta"), "semanas_sin_repetir": summary.get("semanas_sin_repetir"),
            "total": len(items), "fotos": [
                {"id": p["id"], "nombre": p.get("nombre"), "carpeta": p.get("carpeta"), "producto": p.get("producto"),
                 "escena": p.get("escena"), "persona": p.get("persona"), "estado": p.get("estado")}
                for p in items[:_PHOTOS_CAP]]}


def _tool_web(db, conv, client, call_input, employee):
    url = client.get("website_url")
    if not url:
        raise ToolError("El negocio no tiene web cargada en Ajustes.")
    try:
        return {"url": url, "texto": web_text.fetch_text(url)}
    except web_text.WebTextError as exc:
        raise ToolError(str(exc)) from exc


_READ_TOOLS = {
    "leer_marca": _tool_marca, "leer_semana": _tool_semana, "leer_historial": _tool_historial,
    "leer_fotos": _tool_fotos, "leer_web": _tool_web,
}


def _run_tool(db, conv: dict, employee, call, created: list[dict] | None = None) -> tuple[str, bool]:
    """(content, is_error). Tools never receive a client or agency id from the model."""
    is_proposal = call.name in _PROPOSAL_TOOLS
    handler = _READ_TOOLS.get(call.name)
    if handler is None and not is_proposal:
        return f"Herramienta desconocida: {call.name}", True
    try:
        client = client_or_error(db, conv["client_id"], employee.agency_id)
        if is_proposal:
            action = _propose(db, conv, client, call)
            if created is not None:
                created.append(action)
            return (f"Propuesta creada (id {action['id']}). Queda pendiente hasta que una persona la apruebe; "
                    "no digas que ya se aplicó."), False
        return dumps(handler(db, conv, client, call.input or {}, employee)), False
    except ToolError as exc:
        return str(exc), True
    except ServiceError as exc:
        return str(exc.detail), True
    except Exception:
        logger.warning("CM tool %s failed for conversation %s", call.name, conv.get("id"), exc_info=True)
        return "La herramienta falló. Seguí sin esos datos.", True


# ── Propuestas (herramientas proponer_*) ──────────────────────────────────────

_DESCRIPTION_MAX = 500
_TOPICS_MAX = 8
_TOPIC_MAX = 80
_TONE_EXAMPLES_MAX = 6      # same as ClientPatch.tone_examples_shape
_TONE_LINES = 4
_TONE_LINE_MAX = 140
_FOCUS_MAX = 200            # same as the client panel's WeekPatch
_STORY_TEXT_MAX = 300       # same as the editor's text limit (VariantRequest.texto)


def _text(value, label: str) -> str:
    if not isinstance(value, str):
        raise ToolError(f"{label} tiene que ser un texto.")
    return value.strip()


def _motivo(call_input: dict) -> str | None:
    value = call_input.get("motivo")
    return (value.strip()[:300] or None) if isinstance(value, str) else None


def _propose_descripcion(db, conv, client, call_input):
    value = _text(call_input.get("descripcion"), "La descripción")
    if not value:
        raise ToolError("La descripción no puede estar vacía.")
    if len(value) > _DESCRIPTION_MAX:
        raise ToolError(f"La descripción puede tener hasta {_DESCRIPTION_MAX} caracteres (tiene {len(value)}).")
    return ({"descripcion": value, "motivo": _motivo(call_input)},
            {"business_description": client.get("business_description")})


def _propose_temas(db, conv, client, call_input):
    raw = call_input.get("temas")
    if not isinstance(raw, list):
        raise ToolError("Los temas tienen que ser una lista de textos.")
    topics: list[str] = []
    for item in raw:
        topic = _text(item, "Cada tema")
        if not topic:
            raise ToolError("Ningún tema puede estar vacío.")
        if len(topic) > _TOPIC_MAX:
            raise ToolError(f"Cada tema puede tener hasta {_TOPIC_MAX} caracteres.")
        if topic.casefold() not in {t.casefold() for t in topics}:
            topics.append(topic)
    if not topics:
        raise ToolError("Proponé al menos un tema.")
    if len(topics) > _TOPICS_MAX:
        raise ToolError(f"Podés proponer hasta {_TOPICS_MAX} temas (llegaron {len(topics)}).")
    return {"temas": topics, "motivo": _motivo(call_input)}, {"topics": client.get("topics") or []}


def _propose_ejemplo_tono(db, conv, client, call_input):
    raw = call_input.get("historias")
    if not isinstance(raw, list) or len(raw) != _TONE_LINES:
        raise ToolError(f"Un ejemplo de tono tiene que tener exactamente {_TONE_LINES} historias.")
    lines = [_text(line, "Cada historia") for line in raw]
    if any(not line for line in lines):
        raise ToolError("Las historias del ejemplo no pueden estar vacías.")
    if any(len(line) > _TONE_LINE_MAX for line in lines):
        raise ToolError(f"Cada historia del ejemplo puede tener hasta {_TONE_LINE_MAX} caracteres.")
    current = client.get("tone_examples") or []
    if len(current) + 1 > _TONE_EXAMPLES_MAX:
        raise ToolError(f"Ya hay {len(current)} ejemplos de tono y el máximo es {_TONE_EXAMPLES_MAX}: no se puede sumar otro.")
    return {"historias": lines, "motivo": _motivo(call_input)}, {"tone_examples": current}


def _propose_enfoque(db, conv, client, call_input):
    focus = _text(call_input.get("enfoque"), "El enfoque")
    if len(focus) > _FOCUS_MAX:
        raise ToolError(f"El enfoque puede tener hasta {_FOCUS_MAX} caracteres.")
    fmt = call_input.get("formato")
    if fmt is not None and not isinstance(fmt, str):
        raise ToolError("El formato tiene que ser una clave de formato o vacío.")
    if fmt and fmt not in FORMATOS:
        raise ToolError("Formato desconocido: usá una de las claves de formato o dejalo vacío.")
    if not focus and not fmt:
        raise ToolError("No hay nada para proponer: falta el enfoque o el formato.")
    # An empty format means "leave the current one" (None), so a focus-only proposal never clears it.
    return ({"enfoque": focus or None, "formato": fmt or None, "motivo": _motivo(call_input)},
            {"weekly_focus": client.get("weekly_focus"), "weekly_format": client.get("weekly_format")})


def _story_id(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ToolError("Historia no encontrada: usá un id que devolvió leer_semana.") from None


def _propose_texto_historia(db, conv, client, call_input):
    story_id = _story_id(call_input.get("historia_id"))
    text = _text(call_input.get("texto"), "El texto")
    if not text:
        raise ToolError("El texto de la historia no puede estar vacío.")
    if len(text) > _STORY_TEXT_MAX:
        raise ToolError(f"El texto de la historia puede tener hasta {_STORY_TEXT_MAX} caracteres.")
    found = db.table("stories").select("id,client_id,text,estado,image_original_url").eq("id", story_id).eq(
        "client_id", conv["client_id"]).maybe_single().execute()
    story = found.data if found else None
    if not story:
        raise ToolError("Historia no encontrada: usá un id que devolvió leer_semana.")
    if story.get("estado") in stories.LOCKED_STATES:
        raise ToolError("Esa historia ya fue publicada o cancelada y no se puede editar.")
    if not story.get("image_original_url"):
        raise ToolError("Esa historia no tiene imagen original para volver a componer.")
    return ({"historia_id": story_id, "texto": text, "motivo": _motivo(call_input)},
            {"text": story.get("text")})


_PROPOSALS = {
    "proponer_descripcion": ("descripcion", _propose_descripcion),
    "proponer_temas": ("temas", _propose_temas),
    "proponer_ejemplo_tono": ("ejemplo_tono", _propose_ejemplo_tono),
    "proponer_enfoque_semana": ("enfoque_semana", _propose_enfoque),
    "proponer_texto_historia": ("texto_historia", _propose_texto_historia),
}
_PROPOSAL_TOOLS = set(_PROPOSALS)


def _propose(db, conv: dict, client: dict, call) -> dict:
    """Validate and park the proposal. The real field is NOT written here, ever."""
    kind, validate = _PROPOSALS[call.name]
    payload, before = validate(db, conv, client, call.input or {})
    return db.table("cm_pending_actions").insert({
        "conversation_id": conv["id"], "client_id": conv["client_id"], "tool_use_id": call.id,
        "kind": kind, "payload": payload, "before": before, "status": "pending",
    }).execute().data[0]


def action_view(row: dict) -> dict:
    """What the browser sees of an action (no tool_use_id, no internals)."""
    proposal = {key: value for key, value in (row.get("payload") or {}).items() if key != "motivo"}
    return {"id": row["id"], "tipo": row["kind"], "estado": row["status"], "propuesta": proposal,
            "antes": row.get("before"), "motivo": (row.get("payload") or {}).get("motivo"),
            "error": row.get("error"), "created_at": row.get("created_at"), "decided_at": row.get("decided_at")}


def list_actions(db, conversation_id: str) -> list[dict]:
    _recover_stale_claims(db, conversation_id=conversation_id)
    rows = db.table("cm_pending_actions").select("*").eq("conversation_id", conversation_id).order("created_at").execute().data or []
    return [action_view(row) for row in rows]


_DECIDED = ["approved", "discarded", "stale", "failed"]


def _decided_since_last_user_turn(db, conversation_id: str) -> list[dict]:
    """Outcomes the person decided after their previous message: the model hasn't seen them."""
    last = db.table("cm_messages").select("created_at").eq("conversation_id", conversation_id).eq(
        "role", "user").eq("kind", "mensaje").order("seq", desc=True).limit(1).execute().data or []
    since = last[0].get("created_at") if last else None
    if not since:
        return []
    rows = db.table("cm_pending_actions").select("id,kind,status,decided_at").eq(
        "conversation_id", conversation_id).in_("status", _DECIDED).gte("decided_at", since).order(
        "decided_at").execute().data or []
    return [{"id": row["id"], "tipo": row["kind"], "estado": row["status"]} for row in rows]


# ── Aprobar / descartar ───────────────────────────────────────────────────────

_NOT_FOUND_ACTION = "Propuesta no encontrada"
_ALREADY_DECIDED = "Esta propuesta ya se decidió."
_STALE = "El valor cambió desde que se hizo la propuesta: ya no se puede aplicar. Pedile al CM una nueva."
_APPLY_FAILED = "No se pudo aplicar la propuesta."


_APPLYING_TIMEOUT = timedelta(minutes=5)
_INTERRUPTED = "La aplicación se interrumpió; pedí la propuesta de nuevo."


def _recover_stale_claims(db, action_id: str | None = None, conversation_id: str | None = None) -> None:
    """A claim ('applying', stamped in decided_at) older than the timeout means the worker died:
    move it to 'failed' atomically (`where status = 'applying' and decided_at < cutoff`)."""
    now = _now()
    query = db.table("cm_pending_actions").update({
        "status": "failed", "error": _INTERRUPTED, "decided_at": _iso(now),
    }).eq("status", "applying").lt("decided_at", _z(now - _APPLYING_TIMEOUT))
    if action_id:
        query = query.eq("id", action_id)
    if conversation_id:
        query = query.eq("conversation_id", conversation_id)
    try:
        query.execute()
    except Exception:
        logger.warning("CM stale-claim recovery failed", exc_info=True)


def _load_action(db, action_id: str, agency_id: str) -> tuple[dict, dict, dict]:
    """(action, conversation, client). Anything outside the agency answers 404."""
    _recover_stale_claims(db, action_id=action_id)
    result = db.table("cm_pending_actions").select("*").eq("id", action_id).maybe_single().execute()
    action = result.data if result else None
    if not action:
        raise ServiceError(status_code=404, detail=_NOT_FOUND_ACTION)
    try:
        conv, client = load_conversation(db, action["conversation_id"], agency_id)
    except ServiceError as exc:
        raise ServiceError(status_code=404, detail=_NOT_FOUND_ACTION) from exc
    if str(conv["client_id"]) != str(action["client_id"]):
        raise ServiceError(status_code=404, detail=_NOT_FOUND_ACTION)
    return action, conv, client


def _decide(db, action_id: str, changes: dict, employee, only_from: str) -> dict | None:
    rows = db.table("cm_pending_actions").update({
        **changes, "decided_by": employee.id, "decided_at": _iso(_now()),
    }).eq("id", action_id).eq("status", only_from).execute().data
    return rows[0] if rows else None


def discard_action(db, action_id: str, employee) -> dict:
    _load_action(db, action_id, employee.agency_id)
    row = _decide(db, action_id, {"status": "discarded"}, employee, "pending")
    if row is None:
        raise ServiceError(status_code=409, detail=_ALREADY_DECIDED)
    return {"accion": action_view(row)}


def _current_value(db, action: dict, agency_id: str) -> tuple[dict, dict]:
    """(snapshot now, fresh client) in the same shape as `before`."""
    client = client_or_error(db, action["client_id"], agency_id)
    kind, payload = action["kind"], action["payload"]
    if kind == "descripcion":
        return {"business_description": client.get("business_description")}, client
    if kind == "temas":
        return {"topics": client.get("topics") or []}, client
    if kind == "ejemplo_tono":
        return {"tone_examples": client.get("tone_examples") or []}, client
    if kind == "enfoque_semana":
        return {"weekly_focus": client.get("weekly_focus"), "weekly_format": client.get("weekly_format")}, client
    found = db.table("stories").select("id,text").eq("id", payload["historia_id"]).eq(
        "client_id", action["client_id"]).maybe_single().execute()
    story = found.data if found else None
    if not story:
        raise ServiceError(status_code=404, detail="Historia no encontrada")
    return {"text": story.get("text")}, client


def _same_as_before(kind: str, payload: dict, before: dict, current: dict) -> bool:
    if kind == "enfoque_semana":
        # Only the fields the proposal actually changes are compared.
        keys = (["weekly_focus"] if payload.get("enfoque") is not None else []) + (
            ["weekly_format"] if payload.get("formato") else [])
        return all(before.get(k) == current.get(k) for k in keys)
    return before == current


_REVALIDATE_INPUT = {
    "descripcion": lambda p: {"descripcion": p.get("descripcion")},
    "temas": lambda p: {"temas": p.get("temas")},
    "ejemplo_tono": lambda p: {"historias": p.get("historias")},
    "enfoque_semana": lambda p: {"enfoque": p.get("enfoque") or "", "formato": p.get("formato")},
    "texto_historia": lambda p: {"historia_id": p.get("historia_id"), "texto": p.get("texto")},
}
_VALIDATORS = {kind: validate for kind, validate in _PROPOSALS.values()}


def _revalidate(db, conv: dict, client: dict, action: dict) -> dict:
    """Same validators `_propose` uses, run on the stored payload against the FRESH client (and,
    for a story, a fresh read scoped to the conversation's client) as close to the write as possible."""
    payload = action.get("payload")
    if not isinstance(payload, dict) or action["kind"] not in _VALIDATORS:
        raise ServiceError(status_code=409, detail="La propuesta guardada no es válida: pedí una nueva.")
    try:
        checked, _before = _VALIDATORS[action["kind"]](db, conv, client, _REVALIDATE_INPUT[action["kind"]](payload))
    except ToolError as exc:
        raise ServiceError(status_code=409, detail=f"La propuesta ya no se puede aplicar: {exc}") from exc
    return checked


def _apply(db, action: dict, conv: dict, client: dict, employee) -> None:
    """The SAME services the manual edits use: prompt_history through update_client_prompt,
    the weekly format setter, and the story text editor (recomposes the image, learns the correction).

    Remaining non-atomic window: the re-validation and the write are separate requests and the
    RPCs take no compare-and-set, so an edit made in between can still be overwritten."""
    kind = action["kind"]
    payload = _revalidate(db, conv, client, action)
    agency_id, client_id = employee.agency_id, conv["client_id"]
    if kind == "descripcion":
        brand.apply_prompt_patch(db, client_id, employee.id, agency_id, {"business_description": payload["descripcion"]})
    elif kind == "temas":
        brand.apply_prompt_patch(db, client_id, employee.id, agency_id, {"topics": payload["temas"]})
    elif kind == "ejemplo_tono":
        examples = list(client.get("tone_examples") or []) + [payload["historias"]]
        brand.apply_prompt_patch(db, client_id, employee.id, agency_id, {"tone_examples": examples})
    elif kind == "enfoque_semana":
        # Focus first (audited RPC, the step most likely to be rejected); the format setter is the cheap one.
        done_focus = False
        if payload.get("enfoque") is not None:
            brand.apply_prompt_patch(db, client_id, employee.id, agency_id, {"weekly_focus": payload["enfoque"]})
            done_focus = True
        if payload.get("formato"):
            try:
                brand.set_weekly_format(db, client_id, agency_id, payload["formato"])
            except Exception as exc:
                if not done_focus:
                    raise
                if not isinstance(exc, ServiceError):
                    logger.warning("CM action %s: format step failed", action.get("id"), exc_info=True)
                raise ServiceError(status_code=409, detail=(
                    "Se aplicó el enfoque, pero no el formato. Cambialo a mano o pedí la propuesta de nuevo.")) from exc
    else:
        stories.update_story_text(db, payload["historia_id"], agency_id, payload["texto"])


def approve_action(db, action_id: str, employee) -> dict:
    action, conv, client = _load_action(db, action_id, employee.agency_id)
    _require_active(client)
    claimed = db.table("cm_pending_actions").update({"status": "applying", "decided_at": _iso(_now())}).eq(
        "id", action_id).eq("status", "pending").execute().data
    if not claimed:
        raise ServiceError(status_code=409, detail=_ALREADY_DECIDED)
    action = claimed[0]

    # Whatever happens below, the row leaves 'applying': the default outcome is a failure.
    status, error, raised = "failed", "La aplicación se interrumpió.", None
    try:
        before = action.get("before") or {}
        current, fresh = _current_value(db, action, employee.agency_id)
        if not _same_as_before(action["kind"], action["payload"], before, current):
            status, error = "stale", _STALE
            raised = ServiceError(status_code=409, detail=_STALE)
        else:
            _apply(db, action, conv, fresh, employee)
            status, error = "approved", None
    except ServiceError as exc:
        status, error, raised = "failed", str(exc.detail), exc
    except Exception:
        logger.warning("CM action %s failed to apply", action_id, exc_info=True)
        status, error = "failed", _APPLY_FAILED
        raised = ServiceError(status_code=502, detail=_APPLY_FAILED)
    finally:
        try:
            row = _decide(db, action_id, {"status": status, "error": error}, employee, "applying")
        except Exception:
            # Keep the original outcome; a row left 'applying' is recovered as 'failed' after the timeout.
            logger.warning("CM action %s: final decision could not be saved", action_id, exc_info=True)
            row = None
    if raised is not None:
        raise raised
    return {"accion": action_view(row or {**action, "status": status}),
            "resultado": {"tipo": action["kind"], "aplicado": True}}


# ── Persistencia ──────────────────────────────────────────────────────────────

class _Log:
    """Appends rows to cm_messages with consecutive seq (the lease makes it single-writer)."""

    def __init__(self, db, conv: dict, employee):
        self.db, self.conv, self.employee = db, conv, employee
        last = db.table("cm_messages").select("seq").eq("conversation_id", conv["id"]).order(
            "seq", desc=True).limit(1).execute().data or []
        self.seq = int(last[0]["seq"]) if last else 0

    def add(self, role: str, kind: str, content, replay: bool = True, usage=None, stop_reason=None) -> dict:
        self.seq += 1
        return self.db.table("cm_messages").insert({
            "conversation_id": self.conv["id"], "client_id": self.conv["client_id"], "seq": self.seq,
            "role": role, "kind": kind, "content": content, "replay": replay,
            "usage": usage, "stop_reason": stop_reason, "created_by": self.employee.id,
        }).execute().data[0]


def _heal_dangling(db, log: _Log) -> None:
    """A tool_use without its tool_result breaks every later call. The assistant row and its
    tool_result row are two inserts, so a crash between them can leave one dangling: fill the
    missing results (is_error) at the start of the user turn that follows, persisted for good."""
    rows = db.table("cm_messages").select("id,seq,role,kind,content").eq(
        "conversation_id", log.conv["id"]).eq("replay", True).order("seq").execute().data or []
    for index, row in enumerate(rows):
        content = row.get("content")
        if row["role"] != "assistant" or not isinstance(content, list):
            continue
        wanted = [b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")]
        if not wanted:
            continue
        following = rows[index + 1] if index + 1 < len(rows) else None
        if following is not None and following["role"] != "user":
            continue
        existing = following["content"] if following is not None and isinstance(following.get("content"), list) else []
        answered = {b.get("tool_use_id") for b in existing if isinstance(b, dict) and b.get("type") == "tool_result"}
        missing = [{"tool_use_id": i, "content": _UNRUN_TOOL, "is_error": True} for i in wanted if i not in answered]
        if not missing:
            continue
        healed = cm.armar_turno_usuario(None, "", missing)["content"]
        if following is None:
            log.add("user", "tool_result", healed)
        else:
            db.table("cm_messages").update({"content": healed + existing}).eq("id", following["id"]).execute()


def _replayable(db, conversation_id: str) -> list[dict]:
    rows = db.table("cm_messages").select("role,content").eq("conversation_id", conversation_id).eq(
        "replay", True).order("seq").execute().data or []
    return [{"role": row["role"], "content": row["content"]} for row in rows]


# ── Límites y reclamo ─────────────────────────────────────────────────────────

def _start_of_today_utc() -> str:
    local = datetime.now(ZoneInfo(TIMEZONE)).replace(hour=0, minute=0, second=0, microsecond=0)
    return _z(local)


def _check_text(texto: str) -> None:
    limit = get_settings().cm_max_text_chars
    if not texto or len(texto) > limit:
        raise ServiceError(status_code=422, detail=f"El mensaje debe tener entre 1 y {limit} caracteres")


def _check_limits(db, conv: dict, client: dict) -> None:
    settings = get_settings()
    _require_active(client)
    if conv.get("closed_at"):
        raise ServiceError(status_code=409, detail="La conversación está cerrada. Abrí una conversación nueva.")
    if int(conv.get("user_turns") or 0) >= settings.cm_max_user_turns:
        raise ServiceError(status_code=409, detail="La conversación llegó al límite de mensajes. Abrí una conversación nueva.")
    today = db.table("cm_messages").select("id", count="exact").eq("client_id", conv["client_id"]).eq(
        "kind", "mensaje").eq("role", "user").gte("created_at", _start_of_today_utc()).execute()
    if int(today.count or 0) >= settings.cm_max_user_turns_per_client_day:
        raise ServiceError(status_code=429, detail="Se alcanzó el límite diario de mensajes del CM para este cliente.")


def _lease_until(now: datetime) -> str:
    return _z(now + timedelta(seconds=get_settings().cm_busy_seconds))


def _claim(db, conversation_id: str) -> str:
    """Take the conversation and return the owner token. Every later write of the lease
    (renew, release) is conditional on that token, so an expired run cannot touch a new owner's."""
    now = _now()
    token = str(uuid.uuid4())
    claimed = db.table("cm_conversations").update({
        "busy_until": _lease_until(now), "busy_token": token,
    }).eq("id", conversation_id).or_(f"busy_until.is.null,busy_until.lt.{_z(now)}").execute().data
    if not claimed:
        raise ServiceError(status_code=409, detail="El CM todavía está respondiendo. Esperá un momento.")
    return token


def _renew(db, conversation_id: str, token: str) -> None:
    renewed = db.table("cm_conversations").update({"busy_until": _lease_until(_now())}).eq(
        "id", conversation_id).eq("busy_token", token).execute().data
    if not renewed:
        raise ServiceError(status_code=409, detail="Otra solicitud tomó la conversación. Volvé a cargarla.")


def _release(db, conversation_id: str, token: str) -> None:
    try:
        db.table("cm_conversations").update({"busy_until": None, "busy_token": None}).eq(
            "id", conversation_id).eq("busy_token", token).execute()
    except Exception:
        logger.warning("Could not release the CM lease of %s", conversation_id, exc_info=True)


# ── El bucle ──────────────────────────────────────────────────────────────────

def send_message(db, conversation_id: str, employee, texto: str) -> dict:
    texto = (texto or "").strip()
    _check_text(texto)
    load_conversation(db, conversation_id, employee.agency_id)
    token = _claim(db, conversation_id)
    try:
        # Everything read before the claim may be stale by now: read it again.
        conv, client = load_conversation(db, conversation_id, employee.agency_id)
        _check_limits(db, conv, client)
        return _run(db, conv, client, employee, texto, token)
    except APIError as exc:
        if getattr(exc, "code", None) == "23505":  # (conversation_id, seq): another writer got there first
            raise ServiceError(status_code=409, detail="El CM todavía está respondiendo. Esperá un momento.") from exc
        logger.warning("CM database error in conversation %s: %s", conversation_id, exc, exc_info=True)
        raise ServiceError(status_code=503, detail=_DB_UNAVAILABLE) from exc
    finally:
        _release(db, conversation_id, token)


def _run(db, conv: dict, client: dict, employee, texto: str, token: str) -> dict:
    settings = get_settings()
    started = _monotonic()
    # The lease is renewed before every call, so it only covers ONE call (engine timeout + tools).
    # The run budget is separate: no new call starts once one more might not fit in cm_max_run_seconds.
    budget = max(0.0, settings.cm_max_run_seconds - cm.CM_TIMEOUT_SECONDS)
    contexto = ContextoCM(
        hoy=datetime.now(ZoneInfo(TIMEZONE)).date().isoformat(),
        semana=dumps(_compact_week(stories.list_week(db, client["id"], employee.agency_id))),
        acciones_decididas=_decided_since_last_user_turn(db, conv["id"]),
    )
    log = _Log(db, conv, employee)
    user_turn = cm.armar_turno_usuario(contexto, texto, [])
    log.add("user", "mensaje", user_turn["content"])
    db.table("cm_conversations").update({
        "user_turns": int(conv.get("user_turns") or 0) + 1, "updated_at": _iso(_now()),
        "title": conv.get("title") or texto[:60],
    }).eq("id", conv["id"]).execute()
    _heal_dangling(db, log)

    nuevos: list[dict] = []
    acciones: list[dict] = []
    creadas: list[dict] = []
    usadas = 0
    exhausted = False
    for _ in range(settings.cm_max_calls_per_message):
        if usadas and _monotonic() - started > budget:
            exhausted = True
            break
        _renew(db, conv["id"], token)
        usadas += 1
        try:
            turno = cm.turno_cm(conv["system_prompt"], _replayable(db, conv["id"]), conv["tools_version"])
        except (ClaudeGenerationError, ValueError) as exc:
            logger.warning("CM turn failed for conversation %s: %s", conv["id"], exc)
            raise ServiceError(status_code=422, detail="No se pudo consultar a la IA. Tu mensaje quedó guardado; probá de nuevo.") from exc

        if not turno.ejecutable:
            # Audit only: a dangling tool_use without a tool_result would break every later call.
            log.add("assistant", "respuesta", turno.content_blocks, replay=False,
                    usage=turno.usage, stop_reason=turno.stop_reason)
            raise ServiceError(status_code=422, detail="La respuesta de la IA se cortó o fue rechazada. Probá reformular el mensaje.")

        if not turno.llamadas:
            log.add("assistant", "respuesta", turno.content_blocks, usage=turno.usage, stop_reason=turno.stop_reason)
            if turno.texto:
                nuevos.append({"rol": "assistant", "texto": turno.texto})
            break

        resultados = []
        for index, call in enumerate(turno.llamadas):
            if index >= settings.cm_max_tools_per_call:
                content, is_error = f"Superaste el límite de {settings.cm_max_tools_per_call} herramientas por llamada.", True
            else:
                content, is_error = _run_tool(db, conv, employee, call, creadas)
                acciones.append({"herramienta": call.name, "error": is_error})
            resultados.append({"tool_use_id": call.id, "content": content, "is_error": is_error})
        log.add("assistant", "respuesta", turno.content_blocks, usage=turno.usage, stop_reason=turno.stop_reason)
        log.add("user", "tool_result", cm.armar_turno_usuario(None, "", resultados)["content"])
        if turno.texto:
            nuevos.append({"rol": "assistant", "texto": turno.texto})
        exhausted = usadas >= settings.cm_max_calls_per_message
    else:
        exhausted = True

    return {"mensajes_nuevos": nuevos, "acciones": acciones, "propuestas": [action_view(a) for a in creadas],
            "limite": {"llamadas": settings.cm_max_calls_per_message, "usadas": usadas, "agotado": exhausted}}


def agency_cm_enabled(db, agency_id: str) -> bool:
    """The per-agency switch (agencies.cm_enabled); a missing row or column means off."""
    try:
        result = db.table("agencies").select("cm_enabled").eq("id", agency_id).maybe_single().execute()
    except Exception:
        logger.warning("Could not read agencies.cm_enabled for %s", agency_id, exc_info=True)
        return False
    return bool(result and result.data and result.data.get("cm_enabled"))
