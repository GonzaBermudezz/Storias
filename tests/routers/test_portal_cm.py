from __future__ import annotations

from types import SimpleNamespace

import pytest
from cm_memory_db import MemoryDB
from fastapi.testclient import TestClient
from jose import jwt

from app.config import get_settings
from app.deps import require_employee
from app.engine.exceptions import ClaudeGenerationError
from app.engine.schemas import LlamadaHerramienta, TurnoCM
from app.main import app
from app.models.user import Employee
from app.routers import portal_cm
from app.services import cm_agent, stories


def make_db(agency_flag=True):
    return MemoryDB({
        "agencies": [{"id": "a1", "cm_enabled": agency_flag}],
        "clients": [
            {"id": "22222222-2222-4222-8222-bbbbbbbbbbb1", "agency_id": "a1", "name": "Cafe Sol", "active": True, "business_description": "Café", "topics": [], "tone_examples": []},
            {"id": "22222222-2222-4222-8222-bbbbbbbbbbb9", "agency_id": "a2", "name": "Ajeno", "active": True},
        ],
        "cm_conversations": [
            {"id": "11111111-1111-4111-8111-aaaaaaaaaaa1", "client_id": "22222222-2222-4222-8222-bbbbbbbbbbb1", "title": "Plan", "system_prompt": "SYS", "tools_version": "v1",
             "busy_until": None, "user_turns": 0, "closed_at": None, "updated_at": "2026-11-11T11:00:00+00:00"},
            {"id": "11111111-1111-4111-8111-aaaaaaaaaaa9", "client_id": "22222222-2222-4222-8222-bbbbbbbbbbb9", "title": None, "system_prompt": "SYS", "tools_version": "v1",
             "busy_until": None, "user_turns": 0, "closed_at": None, "updated_at": "2026-11-11T11:00:00+00:00"},
        ],
    })


def reply(text):
    return TurnoCM(texto=text, llamadas=[], stop_reason="end_turn", usage={},
                   content_blocks=[{"type": "text", "text": text}])


@pytest.fixture
def api(monkeypatch):
    db = make_db()
    monkeypatch.setattr(portal_cm, "get_admin_client", lambda: db)
    monkeypatch.setattr(get_settings(), "cm_enabled", True)
    monkeypatch.setattr(stories, "list_week", lambda *a, **k: [])
    monkeypatch.setattr(cm_agent.cm, "turno_cm", lambda system, mensajes, version: reply("Hola, soy el CM"))
    monkeypatch.setattr(cm_agent.cm, "iniciar_conversacion_cm",
                        lambda config: SimpleNamespace(system="SYSTEM", version_herramientas="v1"))
    app.dependency_overrides[require_employee] = lambda: Employee(
        id="e1", email="pm@example.com", name="PM", agency_id="a1", role="employee")
    yield SimpleNamespace(http=TestClient(app), db=db)
    app.dependency_overrides.clear()


ENDPOINTS = [
    ("get", "/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones", None),
    ("post", "/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones", {}),
    ("get", "/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1", None),
    ("post", "/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", {"texto": "hola"}),
    ("patch", "/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1", {"titulo": "x"}),
]


@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_env_flag_off_is_404(api, monkeypatch, method, url, body):
    monkeypatch.setattr(get_settings(), "cm_enabled", False)
    assert getattr(api.http, method)(url, **({"json": body} if body is not None else {})).status_code == 404


@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_agency_flag_off_is_404(api, method, url, body):
    api.db.rows("agencies")[0]["cm_enabled"] = False
    assert getattr(api.http, method)(url, **({"json": body} if body is not None else {})).status_code == 404
    assert api.db.writes() == []


def test_client_session_is_rejected(api):
    app.dependency_overrides.clear()
    token = jwt.encode({"sub": "u1", "role": "client", "client_id": "22222222-2222-4222-8222-bbbbbbbbbbb1"}, get_settings().secret_key, algorithm="HS256")
    response = api.http.get("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert api.http.get("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones").status_code == 401


def test_create_and_list_conversations(api):
    created = api.http.post("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones", json={"titulo": "Noviembre"})
    assert created.status_code == 200
    body = created.json()
    assert body["titulo"] == "Noviembre" and body["turnos"] == 0 and body["cerrada"] is False
    row = [r for r in api.db.rows("cm_conversations") if r["id"] == body["id"]][0]
    assert row["system_prompt"] == "SYSTEM" and row["tools_version"] == "v1"
    assert "system_prompt" not in body
    listed = api.http.get("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones").json()
    assert {c["id"] for c in listed} == {"11111111-1111-4111-8111-aaaaaaaaaaa1", body["id"]}


def test_create_rejects_long_title_and_foreign_client(api):
    assert api.http.post("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb1/cm/conversaciones", json={"titulo": "x" * 81}).status_code == 422
    assert api.http.post("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb9/cm/conversaciones", json={}).status_code == 403
    assert api.http.get("/portal/clientes/22222222-2222-4222-8222-bbbbbbbbbbb9/cm/conversaciones").status_code == 403


def test_cross_agency_conversation_is_404(api):
    assert api.http.get("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa9").status_code == 404
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa9/mensajes", json={"texto": "hola"}).status_code == 404
    assert api.http.patch("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa9", json={"titulo": "x"}).status_code == 404
    assert api.http.get("/portal/cm/conversaciones/nope").status_code == 404


def test_send_message_and_read_conversation(api):
    sent = api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "Hola CM"})
    assert sent.status_code == 200
    assert sent.json()["mensajes_nuevos"] == [{"rol": "assistant", "texto": "Hola, soy el CM"}]
    assert sent.json()["limite"]["agotado"] is False
    got = api.http.get("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1").json()
    assert got["conversacion"]["id"] == "11111111-1111-4111-8111-aaaaaaaaaaa1" and got["conversacion"]["turnos"] == 1
    assert [(m["rol"], m["texto"]) for m in got["mensajes"]] == [("user", "Hola CM"), ("assistant", "Hola, soy el CM")]
    assert set(got["mensajes"][0]) == {"id", "rol", "texto", "created_at"}


def test_send_validates_text(api):
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": ""}).status_code == 422
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "x" * 2001}).status_code == 422
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "   "}).status_code == 422


def test_busy_conversation_is_409(api):
    api.db.rows("cm_conversations")[0]["busy_until"] = "2999-01-01T00:00:00+00:00"
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"}).status_code == 409


def test_claude_failure_is_422_and_keeps_the_user_row(api, monkeypatch):
    def boom(system, mensajes, version):
        raise ClaudeGenerationError("caída")
    monkeypatch.setattr(cm_agent.cm, "turno_cm", boom)
    response = api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"})
    assert response.status_code == 422
    assert [r["role"] for r in api.db.rows("cm_messages")] == ["user"]


def test_daily_quota_is_429(api):
    for seq in range(get_settings().cm_max_user_turns_per_client_day):
        api.db.rows("cm_messages").append({"id": f"m{seq}", "conversation_id": "x", "client_id": "22222222-2222-4222-8222-bbbbbbbbbbb1", "seq": seq,
                                           "role": "user", "kind": "mensaje", "created_at": "2999-01-01T00:00:00+00:00"})
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"}).status_code == 429


def test_patch_conversation_renames_and_closes(api):
    response = api.http.patch("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1", json={"titulo": "Nuevo", "cerrada": True})
    assert response.status_code == 200 and response.json()["titulo"] == "Nuevo" and response.json()["cerrada"] is True
    assert api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"}).status_code == 409
    reopened = api.http.patch("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1", json={"cerrada": False}).json()
    assert reopened["cerrada"] is False


def test_get_never_returns_thinking_or_tool_blocks(api, monkeypatch):
    tool = TurnoCM(texto="Miro", llamadas=[LlamadaHerramienta(id="t", name="leer_marca", input={})], stop_reason="tool_use",
                   usage={}, content_blocks=[{"type": "thinking", "thinking": "secreto", "signature": "s"},
                                             {"type": "text", "text": "Miro"},
                                             {"type": "tool_use", "id": "t", "name": "leer_marca", "input": {}}])
    queue = [tool, reply("Listo")]
    monkeypatch.setattr(cm_agent.cm, "turno_cm", lambda system, mensajes, version: queue.pop(0))
    api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"})
    dump = api.http.get("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1").text
    assert "secreto" not in dump and "tool_use" not in dump and "tool_result" not in dump and "Cafe Sol" not in dump


def test_me_exposes_cm_enabled_only_with_both_flags(api, monkeypatch):
    from app.routers import portal
    monkeypatch.setattr(portal, "get_admin_client", lambda: api.db)
    assert api.http.get("/portal/me").json()["cm_enabled"] is True
    api.db.rows("agencies")[0]["cm_enabled"] = False
    assert api.http.get("/portal/me").json()["cm_enabled"] is False
    monkeypatch.setattr(get_settings(), "cm_enabled", False)
    assert not api.http.get("/portal/me").json().get("cm_enabled")


@pytest.mark.parametrize("method,url,body", [
    ("get", "/portal/clientes/not-a-uuid/cm/conversaciones", None),
    ("post", "/portal/clientes/not-a-uuid/cm/conversaciones", {}),
    ("get", "/portal/cm/conversaciones/not-a-uuid", None),
    ("post", "/portal/cm/conversaciones/not-a-uuid/mensajes", {"texto": "hola"}),
    ("patch", "/portal/cm/conversaciones/not-a-uuid", {"titulo": "x"}),
])
def test_malformed_ids_are_404_not_500(api, method, url, body):
    response = getattr(api.http, method)(url, **({"json": body} if body is not None else {}))
    assert response.status_code == 404
    assert [entry for entry in api.db.log if entry[0] != "agencies"] == []  # never reaches the database


def test_malformed_id_message_matches_not_found(api):
    unknown = api.http.get("/portal/cm/conversaciones/33333333-3333-4333-8333-333333333333")
    malformed = api.http.get("/portal/cm/conversaciones/not-a-uuid")
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json() == malformed.json()
    unknown = api.http.get("/portal/clientes/33333333-3333-4333-8333-333333333333/cm/conversaciones")
    malformed = api.http.get("/portal/clientes/not-a-uuid/cm/conversaciones")
    assert unknown.json() == malformed.json()


def test_database_error_inside_the_run_is_a_generic_503(api, monkeypatch):
    from postgrest.exceptions import APIError

    def boom(system, mensajes, version):
        raise APIError({"message": "relation secret_table exploded", "code": "XX000"})
    monkeypatch.setattr(cm_agent.cm, "turno_cm", boom)
    response = api.http.post("/portal/cm/conversaciones/11111111-1111-4111-8111-aaaaaaaaaaa1/mensajes", json={"texto": "hola"})
    assert response.status_code == 503
    assert "secret_table" not in response.text
    assert api.db.rows("cm_conversations")[0]["busy_until"] is None
