from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cm_memory_db import MemoryDB
from postgrest.exceptions import APIError

from app.config import get_settings
from app.engine.exceptions import ClaudeGenerationError
from app.engine.schemas import LlamadaHerramienta, TurnoCM
from app.services import cm_agent, photos, stories, web_text
from app.services.errors import ServiceError

EMPLOYEE = SimpleNamespace(id="e1", agency_id="a1")


def make_db(**conversation):
    conv = {"id": "conv1", "client_id": "c1", "created_by": "e1", "title": None,
            "system_prompt": "SYS", "tools_version": "v1", "busy_until": None,
            "user_turns": 0, "closed_at": None, "updated_at": "2026-11-11T11:00:00+00:00", **conversation}
    return MemoryDB({
        "clients": [{"id": "c1", "agency_id": "a1", "active": True, "name": "Cafe Sol", "business_description": "Café de barrio",
                     "weekly_focus": None, "weekly_format": None, "voice_style": None, "topics": ["granos"],
                     "tone_examples": [], "website_url": "https://cafe.example",
                     "drive_folder_id": "folder", "drive_refresh_token_encrypted": None}],
        "cm_conversations": [conv],
    })


def tool_use(call_id, name, input=None):
    return {"type": "tool_use", "id": call_id, "name": name, "input": input or {}}


def turno(texto="", calls=(), stop="end_turn", extra_blocks=(), ejecutable=True):
    blocks = [*extra_blocks]
    if texto:
        blocks.append({"type": "text", "text": texto})
    blocks += [tool_use(i, n, inp) for i, n, inp in calls]
    return TurnoCM(
        texto=texto, llamadas=[LlamadaHerramienta(id=i, name=n, input=inp or {}) for i, n, inp in calls] if ejecutable else [],
        stop_reason=stop, usage={"input_tokens": 1}, content_blocks=blocks, ejecutable=ejecutable,
    )


@pytest.fixture
def script(monkeypatch):
    """Fake Claude: pops one TurnoCM (or exception) per call and records what it was sent."""
    state = SimpleNamespace(queue=[], calls=[])

    def fake_turno(system, mensajes, version):
        state.calls.append({"system": system, "mensajes": copy.deepcopy(mensajes), "version": version})
        if state.hook:
            state.hook(len(state.calls))
        item = state.queue.pop(0) if len(state.queue) > 1 or not state.repeat else state.queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    state.repeat = False
    state.hook = None
    monkeypatch.setattr(cm_agent.cm, "turno_cm", fake_turno)
    monkeypatch.setattr(stories, "list_week", lambda db, client_id, agency_id: [
        {"scheduled_date": "2026-11-16", "stories": [{"id": "s1", "order": 1, "text": "Hola", "estado": "pendiente", "aprobado": False}]}])
    return state


def send(db, texto="Hola CM"):
    return cm_agent.send_message(db, "conv1", EMPLOYEE, texto)


def test_plain_answer_persists_both_rows_and_releases_the_lease(script):
    db = make_db()
    script.queue = [turno("Todo bien")]
    result = send(db)

    rows = db.rows("cm_messages")
    assert [(r["seq"], r["role"], r["kind"], r["replay"]) for r in rows] == [
        (1, "user", "mensaje", True), (2, "assistant", "respuesta", True)]
    assert rows[0]["created_by"] == "e1" and rows[0]["client_id"] == "c1"
    assert "<contexto_actual>" in rows[0]["content"][0]["text"] and rows[0]["content"][0]["text"].endswith("Hola CM")
    assert rows[1]["content"] == [{"type": "text", "text": "Todo bien"}]
    assert rows[1]["usage"] == {"input_tokens": 1} and rows[1]["stop_reason"] == "end_turn"
    conv = db.rows("cm_conversations")[0]
    assert conv["busy_until"] is None and conv["user_turns"] == 1 and conv["title"] == "Hola CM"
    assert script.calls[0]["system"] == "SYS" and script.calls[0]["version"] == "v1"
    assert result["mensajes_nuevos"] == [{"rol": "assistant", "texto": "Todo bien"}]
    assert result["limite"]["agotado"] is False


def test_context_carries_argentina_date_and_compact_week(script):
    db = make_db()
    script.queue = [turno("ok")]
    send(db)
    text = script.calls[0]["mensajes"][0]["content"][0]["text"]
    assert "Hoy: " in text and "2026-11-16" in text and "s1" in text


def test_read_tool_loop_replays_blocks_unchanged(script):
    db = make_db()
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    first = turno("Miro la marca", [("tu1", "leer_marca", {})], stop="tool_use", extra_blocks=[thinking])
    script.queue = [first, turno("Es un café")]
    result = send(db)

    assert len(script.calls) == 2
    second = script.calls[1]["mensajes"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == first.content_blocks  # thinking + tool_use untouched
    block = second[2]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "tu1" and "is_error" not in block
    assert "Cafe Sol" in block["content"] and "<datos_no_confiables" in block["content"]
    kinds = [(r["role"], r["kind"]) for r in db.rows("cm_messages")]
    assert kinds == [("user", "mensaje"), ("assistant", "respuesta"), ("user", "tool_result"), ("assistant", "respuesta")]
    assert result["acciones"] == [{"herramienta": "leer_marca", "error": False}]
    assert [m["texto"] for m in result["mensajes_nuevos"]] == ["Miro la marca", "Es un café"]


def test_tool_results_are_deterministic_json(script):
    db = make_db()
    db.rows("clients")[0]["business_description"] = "Café ñandú"
    script.queue = [turno("", [("t1", "leer_marca", {})], stop="tool_use"), turno("fin")]
    send(db)
    content = script.calls[1]["mensajes"][2]["content"][0]["content"]
    payload = content[content.index("{"): content.rindex("}") + 1]
    data = json.loads(payload)
    assert payload == json.dumps(data, sort_keys=True, ensure_ascii=False)
    assert "Café ñandú" in payload


def test_call_cap_stops_after_six_calls_and_keeps_history_consistent(script):
    db = make_db()
    script.queue = [turno("", [("t", "leer_marca", {})], stop="tool_use")]
    script.repeat = True
    result = send(db)
    assert len(script.calls) == 6
    assert result["limite"] == {"llamadas": 6, "usadas": 6, "agotado": True}
    rows = db.rows("cm_messages")
    assert rows[-1]["kind"] == "tool_result"  # no dangling tool_use


def test_extra_tools_beyond_five_get_an_error(script):
    db = make_db()
    calls = [(f"t{i}", "leer_marca", {}) for i in range(7)]
    script.queue = [turno("", calls, stop="tool_use"), turno("fin")]
    send(db)
    blocks = script.calls[1]["mensajes"][2]["content"]
    assert len(blocks) == 7
    assert [b.get("is_error", False) for b in blocks] == [False] * 5 + [True, True]
    assert "límite" in blocks[5]["content"]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_unusable_stop_reason_runs_no_tools_and_is_audit_only(script, stop):
    db = make_db()
    script.queue = [turno("a medias", [], stop=stop, ejecutable=False,
                          extra_blocks=[tool_use("x", "leer_marca", {})])]
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 422
    rows = db.rows("cm_messages")
    assert [(r["role"], r["replay"]) for r in rows] == [("user", True), ("assistant", False)]
    assert db.rows("cm_conversations")[0]["busy_until"] is None


def test_non_replayable_rows_are_excluded_from_the_next_call(script):
    db = make_db()
    script.queue = [turno("roto", stop="max_tokens", ejecutable=False)]
    with pytest.raises(ServiceError):
        send(db)
    script.queue = [turno("bien")]
    send(db, "Otra vez")
    sent = script.calls[1]["mensajes"]
    assert all(m["role"] == "user" for m in sent) and len(sent) == 2
    assert "roto" not in json.dumps(sent)


def test_proposals_are_not_enabled_yet(script):
    db = make_db()
    script.queue = [turno("", [("p1", "proponer_descripcion", {"descripcion": "x", "motivo": "y"})], stop="tool_use"), turno("fin")]
    send(db)
    block = script.calls[1]["mensajes"][2]["content"][0]
    assert block["is_error"] is True and "todavía no están habilitadas" in block["content"]
    assert db.writes("clients") == []


def test_unknown_tool_is_an_error(script):
    db = make_db()
    script.queue = [turno("", [("u1", "borrar_todo", {})], stop="tool_use"), turno("fin")]
    send(db)
    assert script.calls[1]["mensajes"][2]["content"][0]["is_error"] is True


def test_leer_web_reads_the_bound_website(script, monkeypatch):
    db = make_db()
    seen = []
    monkeypatch.setattr(web_text, "fetch_text", lambda url: seen.append(url) or "Sitio del café")
    script.queue = [turno("", [("w1", "leer_web", {})], stop="tool_use"), turno("fin")]
    send(db)
    assert seen == ["https://cafe.example"]
    assert "Sitio del café" in script.calls[1]["mensajes"][2]["content"][0]["content"]


def test_leer_web_failure_is_a_tool_error(script, monkeypatch):
    db = make_db()

    def boom(url):
        raise web_text.WebTextError("No se pudo leer la web")
    monkeypatch.setattr(web_text, "fetch_text", boom)
    script.queue = [turno("", [("w1", "leer_web", {})], stop="tool_use"), turno("fin")]
    send(db)
    block = script.calls[1]["mensajes"][2]["content"][0]
    assert block["is_error"] is True and "No se pudo leer la web" in block["content"]


def test_leer_web_without_website_is_a_tool_error(script):
    db = make_db()
    db.rows("clients")[0]["website_url"] = None
    script.queue = [turno("", [("w1", "leer_web", {})], stop="tool_use"), turno("fin")]
    send(db)
    assert script.calls[1]["mensajes"][2]["content"][0]["is_error"] is True


def test_leer_fotos_is_bound_to_the_conversation_client_and_filters(script, monkeypatch):
    db = make_db()
    seen = []

    def fake_summary(db_, client, refresh_token, on_listed=None):
        seen.append(client["id"])
        return {"carpeta": True, "semanas_sin_repetir": 4, "fotos": [
            {"id": "f1", "nombre": "a.jpg", "carpeta": None, "producto": "taza", "escena": "Producto", "persona": False, "estado": "nueva", "analizada": True},
            {"id": "f2", "nombre": "b.jpg", "carpeta": None, "producto": None, "escena": None, "persona": False, "estado": "reciente", "analizada": False}]}
    monkeypatch.setattr(photos, "photos_summary", fake_summary)
    script.queue = [turno("", [("f", "leer_fotos", {"solo_disponibles": True})], stop="tool_use"), turno("fin")]
    send(db)
    content = script.calls[1]["mensajes"][2]["content"][0]["content"]
    assert seen == ["c1"] and "a.jpg" in content and "b.jpg" not in content


def test_leer_fotos_service_error_becomes_tool_error(script, monkeypatch):
    db = make_db()

    def fail(*args, **kwargs):
        raise ServiceError(status_code=422, detail="Drive caído")
    monkeypatch.setattr(photos, "photos_summary", fail)
    script.queue = [turno("", [("f", "leer_fotos", {})], stop="tool_use"), turno("fin")]
    send(db)
    block = script.calls[1]["mensajes"][2]["content"][0]
    assert block["is_error"] is True and "Drive caído" in block["content"]


def test_claude_failure_is_422_and_keeps_the_user_message(script):
    db = make_db()
    script.queue = [ClaudeGenerationError("api caída")]
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 422
    assert [r["role"] for r in db.rows("cm_messages")] == ["user"]
    assert db.rows("cm_conversations")[0]["busy_until"] is None


def test_busy_conversation_is_409_and_is_not_touched(script):
    future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    db = make_db(busy_until=future)
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409
    assert db.rows("cm_messages") == [] and script.calls == []
    assert db.rows("cm_conversations")[0]["busy_until"] == future


def test_expired_lease_can_be_claimed(script):
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    db = make_db(busy_until=past)
    script.queue = [turno("ok")]
    send(db)
    assert db.rows("cm_conversations")[0]["busy_until"] is None


def test_conversation_of_another_agency_is_404(script):
    db = make_db()
    db.rows("clients")[0]["agency_id"] = "other"
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 404 and script.calls == []


def test_unknown_conversation_is_404(script):
    with pytest.raises(ServiceError) as err:
        cm_agent.send_message(make_db(), "nope", EMPLOYEE, "hola")
    assert err.value.status_code == 404


def test_daily_quota_is_429(script):
    db = make_db()
    today = datetime.now(timezone.utc).isoformat()
    for seq in range(get_settings().cm_max_user_turns_per_client_day):
        db.rows("cm_messages").append({"id": f"m{seq}", "conversation_id": "other", "client_id": "c1", "seq": seq,
                                       "role": "user", "kind": "mensaje", "created_at": today})
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 429 and script.calls == []


def test_conversation_turn_cap_is_409(script):
    db = make_db(user_turns=get_settings().cm_max_user_turns)
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409 and "conversación nueva" in err.value.detail


def test_closed_conversation_is_409(script):
    db = make_db(closed_at="2026-11-10T00:00:00+00:00")
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409


def test_text_length_is_validated(script):
    with pytest.raises(ServiceError) as err:
        send(make_db(), "x" * (get_settings().cm_max_text_chars + 1))
    assert err.value.status_code == 422
    with pytest.raises(ServiceError):
        send(make_db(), "   ")


def test_visible_messages_hide_thinking_tools_and_context(script):
    db = make_db()
    thinking = {"type": "thinking", "thinking": "secreto", "signature": "s"}
    script.queue = [turno("Miro", [("t", "leer_marca", {})], stop="tool_use", extra_blocks=[thinking]), turno("Listo")]
    send(db)
    visible = cm_agent.visible_messages(db, "conv1")
    assert [(m["rol"], m["texto"]) for m in visible] == [("user", "Hola CM"), ("assistant", "Miro"), ("assistant", "Listo")]
    dump = json.dumps(visible)
    assert "secreto" not in dump and "thinking" not in dump and "contexto_actual" not in dump and "Cafe Sol" not in dump


def test_visible_messages_skip_audit_only_rows(script):
    db = make_db()
    script.queue = [turno("roto", stop="max_tokens", ejecutable=False)]
    with pytest.raises(ServiceError):
        send(db)
    assert [m["rol"] for m in cm_agent.visible_messages(db, "conv1")] == ["user"]


def test_create_conversation_freezes_system_and_tools_version(monkeypatch):
    db = make_db()
    monkeypatch.setattr(cm_agent.cm, "iniciar_conversacion_cm",
                        lambda config: SimpleNamespace(system=f"SYSTEM {config.nombre_negocio}", version_herramientas="v1"))
    conv = cm_agent.create_conversation(db, "c1", EMPLOYEE, "  Plan de noviembre ")
    row = db.rows("cm_conversations")[-1]
    assert conv["id"] == row["id"]
    assert row["system_prompt"] == "SYSTEM Cafe Sol" and row["tools_version"] == "v1"
    assert row["client_id"] == "c1" and row["created_by"] == "e1" and row["title"] == "Plan de noviembre"


def test_create_conversation_for_foreign_client_is_403():
    db = make_db()
    db.rows("clients")[0]["agency_id"] = "other"
    with pytest.raises(ServiceError) as err:
        cm_agent.create_conversation(db, "c1", EMPLOYEE, None)
    assert err.value.status_code == 403


def test_list_conversations_is_scoped_to_the_client():
    db = make_db()
    db.rows("cm_conversations").append({"id": "x", "client_id": "other", "updated_at": "2026-11-11T10:00:00+00:00"})
    listed = cm_agent.list_conversations(db, "c1", "a1")
    assert [c["id"] for c in listed] == ["conv1"]
    assert set(listed[0]) == {"id", "titulo", "created_at", "updated_at", "cerrada", "turnos"}


# ── Fixes of the slice 4a review ──────────────────────────────────────────────

def conv_row(db):
    return db.rows("cm_conversations")[0]


def test_claim_filter_is_exact_and_timestamps_use_z_suffix(script, monkeypatch):
    db = make_db()
    monkeypatch.setattr(cm_agent, "_now", lambda: datetime(2026, 11, 11, 16, 0, 0, tzinfo=timezone.utc))
    seen = []
    script.hook = lambda n: seen.append(conv_row(db)["busy_until"])
    script.queue = [turno("ok")]
    send(db)
    assert db.filters == ["busy_until.is.null,busy_until.lt.2026-11-11T16:00:00Z"]
    assert seen == ["2026-11-11T16:01:30Z"]


def test_lease_has_an_owner_token_and_release_clears_both(script):
    db = make_db()
    tokens = []
    script.hook = lambda n: tokens.append(conv_row(db)["busy_token"])
    script.queue = [turno("ok")]
    send(db)
    assert tokens[0] and conv_row(db)["busy_token"] is None and conv_row(db)["busy_until"] is None


def test_lease_is_renewed_before_every_claude_call(script, monkeypatch):
    db = make_db()
    monkeypatch.setattr(get_settings(), "cm_busy_seconds", 600)
    clock = SimpleNamespace(now=datetime(2026, 11, 11, 16, 0, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cm_agent, "_now", lambda: clock.now)
    seen = []

    def hook(n):
        seen.append(conv_row(db)["busy_until"])
        clock.now += timedelta(seconds=10)
    script.hook = hook
    script.queue = [turno("", [("t", "leer_marca", {})], stop="tool_use"), turno("fin")]
    send(db)
    assert seen == ["2026-11-11T16:10:00Z", "2026-11-11T16:10:10Z"]


def test_lost_lease_stops_the_loop_and_never_clears_the_new_owners_lease(script):
    db = make_db()
    future = "2999-01-01T00:00:00Z"

    def hook(n):
        conv_row(db).update({"busy_token": "someone-else", "busy_until": future})
    script.hook = hook
    script.queue = [turno("", [("t", "leer_marca", {})], stop="tool_use"), turno("fin")]
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409
    assert len(script.calls) == 1
    assert conv_row(db)["busy_token"] == "someone-else" and conv_row(db)["busy_until"] == future


def test_wall_clock_budget_stops_new_claude_calls_under_the_lease(script, monkeypatch):
    db = make_db()
    clock = SimpleNamespace(t=0.0)
    monkeypatch.setattr(cm_agent, "_monotonic", lambda: clock.t)

    def hook(n):
        clock.t += 30.0
    script.hook = hook
    script.queue = [turno("", [("t", "leer_marca", {})], stop="tool_use")]
    script.repeat = True
    result = send(db)
    # The lease is renewed before every call, so it only has to cover ONE call: the run
    # budget is cm_max_run_seconds (150) minus one engine timeout (60) = 90 s to start
    # calls. At 30 s per call: calls start at 0, 30, 60 and 90; at 120 it stops.
    assert len(script.calls) == 4
    assert result["limite"]["agotado"] is True and result["limite"]["usadas"] == 4
    assert db.rows("cm_messages")[-1]["kind"] == "tool_result"
    assert conv_row(db)["busy_until"] is None


def seed_dangling(db, results=None):
    rows = db.rows("cm_messages")
    rows.append({"id": "m1", "conversation_id": "conv1", "client_id": "c1", "seq": 1, "role": "user", "kind": "mensaje",
                 "content": [{"type": "text", "text": "Antes"}], "replay": True})
    rows.append({"id": "m2", "conversation_id": "conv1", "client_id": "c1", "seq": 2, "role": "assistant", "kind": "respuesta",
                 "content": [tool_use("a", "leer_marca"), tool_use("b", "leer_marca")], "replay": True})
    if results is not None:
        rows.append({"id": "m3", "conversation_id": "conv1", "client_id": "c1", "seq": 3, "role": "user", "kind": "tool_result",
                     "content": results, "replay": True})


def test_dangling_tool_use_is_healed_in_the_next_user_turn_and_persisted(script):
    db = make_db()
    seed_dangling(db)
    script.queue = [turno("bien")]
    send(db, "Seguimos")
    sent = script.calls[0]["mensajes"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    blocks = sent[2]["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result", "text"]
    assert [b["tool_use_id"] for b in blocks[:2]] == ["a", "b"]
    assert all(b["is_error"] is True and "no llegó a ejecutarse" in b["content"] for b in blocks[:2])
    stored = [r for r in db.rows("cm_messages") if r["seq"] == 3][0]
    assert [b["type"] for b in stored["content"]] == ["tool_result", "tool_result", "text"]


def test_partially_answered_tool_use_only_gets_the_missing_result(script):
    db = make_db()
    seed_dangling(db, results=[{"type": "tool_result", "tool_use_id": "a", "content": "ok"}])
    script.queue = [turno("bien")]
    send(db, "Seguimos")
    sent = script.calls[0]["mensajes"]
    blocks = sent[2]["content"]
    assert sorted(b["tool_use_id"] for b in blocks) == ["a", "b"]
    assert [b for b in blocks if b["tool_use_id"] == "a"][0]["content"] == "ok"
    assert [b for b in blocks if b["tool_use_id"] == "b"][0]["is_error"] is True
    assert sent[3]["content"][0]["type"] == "text"


def test_healthy_history_is_left_untouched(script):
    db = make_db()
    script.queue = [turno("", [("t", "leer_marca", {})], stop="tool_use"), turno("fin")]
    send(db)
    before = copy.deepcopy(db.rows("cm_messages"))
    script.queue = [turno("otra")]
    send(db, "Otra")
    assert db.rows("cm_messages")[: len(before)] == before


def test_seq_unique_violation_is_409(script, monkeypatch):
    db = make_db()
    db.rows("cm_messages").append({"id": "x", "conversation_id": "conv1", "client_id": "c1", "seq": 1,
                                   "role": "user", "kind": "mensaje", "content": [], "replay": False})

    def stale_init(self, db_, conv, employee):
        self.db, self.conv, self.employee, self.seq = db_, conv, employee, 0
    monkeypatch.setattr(cm_agent._Log, "__init__", stale_init)
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409
    assert conv_row(db)["busy_token"] is None


def claiming_with(monkeypatch, mutate):
    original = cm_agent._claim

    def claim(db, conversation_id):
        token = original(db, conversation_id)
        mutate(db)
        return token
    monkeypatch.setattr(cm_agent, "_claim", claim)


def test_closed_while_waiting_for_the_claim_is_409(script, monkeypatch):
    db = make_db()
    claiming_with(monkeypatch, lambda d: conv_row(d).update({"closed_at": "2026-11-11T11:59:00+00:00"}))
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409 and script.calls == [] and db.rows("cm_messages") == []
    assert conv_row(db)["busy_token"] is None


def test_turn_cap_is_rechecked_after_the_claim(script, monkeypatch):
    db = make_db()
    claiming_with(monkeypatch, lambda d: conv_row(d).update({"user_turns": get_settings().cm_max_user_turns}))
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409 and script.calls == []


def test_daily_quota_is_rechecked_after_the_claim(script, monkeypatch):
    db = make_db()

    def fill(d):
        for seq in range(get_settings().cm_max_user_turns_per_client_day):
            d.rows("cm_messages").append({"id": f"q{seq}", "conversation_id": "other", "client_id": "c1", "seq": seq,
                                          "role": "user", "kind": "mensaje", "created_at": "2999-01-01T00:00:00+00:00"})
    claiming_with(monkeypatch, fill)
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 429 and script.calls == []


def test_user_turns_increment_from_the_fresh_value(script, monkeypatch):
    db = make_db()
    claiming_with(monkeypatch, lambda d: conv_row(d).update({"user_turns": 5}))
    script.queue = [turno("ok")]
    send(db)
    assert conv_row(db)["user_turns"] == 6


def test_database_error_in_the_run_is_503_and_releases_the_lease(script):
    db = make_db()

    def explode(n):
        raise APIError({"message": "tabla interna rota", "code": "XX000"})
    script.hook = explode
    script.queue = [turno("ok")]
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 503 and "tabla" not in err.value.detail
    assert conv_row(db)["busy_token"] is None and conv_row(db)["busy_until"] is None


def test_inactive_client_cannot_start_or_continue_a_conversation(script):
    db = make_db()
    db.rows("clients")[0]["active"] = False
    with pytest.raises(ServiceError) as err:
        cm_agent.create_conversation(db, "c1", EMPLOYEE, None)
    assert err.value.status_code == 409
    with pytest.raises(ServiceError) as err:
        send(db)
    assert err.value.status_code == 409 and script.calls == [] and db.rows("cm_messages") == []
    assert conv_row(db)["busy_token"] is None
