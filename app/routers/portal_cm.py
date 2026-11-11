"""CM con IA: conversaciones con el equipo (CLAUDE.md, sección 16).

Solo empleados de la consola (el panel /app del cliente no entra: `require_employee`
rechaza su sesión). Necesita DOS interruptores: `Settings.cm_enabled` y
`agencies.cm_enabled`; apagado es 404 en todos los endpoints.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db.supabase import get_admin_client
from app.deps import require_employee
from app.models.user import Employee
from app.services import cm_agent

router = APIRouter(prefix="/portal", tags=["portal-cm"])


def require_cm_employee(employee: Annotated[Employee, Depends(require_employee)]) -> Employee:
    if not get_settings().cm_enabled or not cm_agent.agency_cm_enabled(get_admin_client(), employee.agency_id):
        raise HTTPException(status_code=404, detail="No encontrado")
    return employee


CmEmployeeDep = Annotated[Employee, Depends(require_cm_employee)]


def _uuid_or_404(value: str, detail: str) -> str:
    """A malformed id is answered exactly like one that doesn't exist: Postgres would
    otherwise raise on the uuid cast and the person would see a 500."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=detail) from None


def _client_id(value: str) -> str:
    return _uuid_or_404(value, "Cliente no encontrado")


def _conversation_id(value: str) -> str:
    return _uuid_or_404(value, "Conversación no encontrada")


def _action_id(value: str) -> str:
    return _uuid_or_404(value, "Propuesta no encontrada")


class ConversationCreate(BaseModel):
    titulo: str | None = Field(default=None, max_length=80)


class ConversationPatch(BaseModel):
    titulo: str | None = Field(default=None, max_length=80)
    cerrada: bool | None = None


class MessageBody(BaseModel):
    # The real ceiling is Settings.cm_max_text_chars (checked by the service).
    texto: str = Field(min_length=1, max_length=2000)


@router.get("/clientes/{client_id}/cm/conversaciones")
def list_conversations(client_id: str, employee: CmEmployeeDep):
    return cm_agent.list_conversations(get_admin_client(), _client_id(client_id), employee.agency_id)


@router.post("/clientes/{client_id}/cm/conversaciones")
def create_conversation(client_id: str, body: ConversationCreate, employee: CmEmployeeDep):
    return cm_agent.create_conversation(get_admin_client(), _client_id(client_id), employee, body.titulo)


@router.get("/cm/conversaciones/{conversation_id}")
def get_conversation(conversation_id: str, employee: CmEmployeeDep):
    db = get_admin_client()
    conversation_id = _conversation_id(conversation_id)
    conv, _ = cm_agent.load_conversation(db, conversation_id, employee.agency_id)
    return {"conversacion": cm_agent.conversation_summary(conv),
            "mensajes": cm_agent.visible_messages(db, conversation_id),
            "acciones": cm_agent.list_actions(db, conversation_id)}


@router.post("/cm/conversaciones/{conversation_id}/mensajes")
def send_message(conversation_id: str, body: MessageBody, employee: CmEmployeeDep):
    return cm_agent.send_message(get_admin_client(), _conversation_id(conversation_id), employee, body.texto)


@router.patch("/cm/conversaciones/{conversation_id}")
def patch_conversation(conversation_id: str, body: ConversationPatch, employee: CmEmployeeDep):
    return cm_agent.update_conversation(get_admin_client(), _conversation_id(conversation_id), employee.agency_id, body.titulo, body.cerrada)


@router.post("/cm/acciones/{action_id}/aprobar")
def approve_action(action_id: str, employee: CmEmployeeDep):
    return cm_agent.approve_action(get_admin_client(), _action_id(action_id), employee)


@router.post("/cm/acciones/{action_id}/descartar")
def discard_action(action_id: str, employee: CmEmployeeDep):
    return cm_agent.discard_action(get_admin_client(), _action_id(action_id), employee)
