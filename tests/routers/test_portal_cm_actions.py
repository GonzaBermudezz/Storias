"""CM con IA, etapa 4b: aprobar y descartar propuestas."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_portal_cm import api  # noqa: F401  (fixture)

from app.config import get_settings
from app.engine.formatos import FORMATOS
from app.engine.schemas import ImagenEditada, LlamadaHerramienta, TurnoCM
from app.services import cm_agent, stories

CLIENT = "22222222-2222-4222-8222-bbbbbbbbbbb1"
FOREIGN_CLIENT = "22222222-2222-4222-8222-bbbbbbbbbbb9"
CONV = "11111111-1111-4111-8111-aaaaaaaaaaa1"
FOREIGN_CONV = "11111111-1111-4111-8111-aaaaaaaaaaa9"
STORY = "44444444-4444-4444-8444-000000000001"
FORMAT = next(iter(FORMATOS))
APPROVE, DISCARD = "/portal/cm/acciones/{}/aprobar", "/portal/cm/acciones/{}/descartar"

_counter = iter(range(1, 1000))


def action(api, kind, payload, before, conv=CONV, client=CLIENT, status="pending"):
    row = {"id": f"55555555-5555-4555-8555-{next(_counter):012d}", "conversation_id": conv, "client_id": client,
           "tool_use_id": f"t{next(_counter)}", "kind": kind, "payload": payload, "before": before,
           "status": status, "error": None, "decided_by": None, "decided_at": None,
           "created_at": "2026-11-11T12:00:00+00:00"}
    api.db.rows("cm_pending_actions").append(row)
    return row


def stored(api, row):
    return next(r for r in api.db.rows("cm_pending_actions") if r["id"] == row["id"])


def client_row(api):
    return next(r for r in api.db.rows("clients") if r["id"] == CLIENT)


@pytest.fixture
def seeded(api):
    client_row(api).update({"business_description": "Café", "topics": ["granos"], "tone_examples": [["a", "b", "c", "d"]],
                            "weekly_focus": "Viejo", "weekly_format": None, "name": "Cafe Sol"})
    api.db.tables["stories"] = [{"id": STORY, "client_id": CLIENT, "order": 2, "text": "Texto viejo", "estado": "pendiente",
                                 "image_original_url": "https://img/o.png", "image_url": "https://img/old.png", "font_choice": None}]
    api.db.tables["prompt_history"] = []
    return api


def approve(api, row):
    return api.http.post(APPROVE.format(row["id"]))


def test_approve_description_goes_through_the_audited_rpc(seeded):
    row = action(seeded, "descripcion", {"descripcion": "Café de especialidad", "motivo": "m"}, {"business_description": "Café"})
    response = approve(seeded, row)
    assert response.status_code == 200
    assert response.json()["accion"]["estado"] == "approved" and response.json()["resultado"]["aplicado"] is True
    assert client_row(seeded)["business_description"] == "Café de especialidad"
    history = seeded.db.rows("prompt_history")
    assert [(h["field"], h["old_value"], h["new_value"], h["changed_by"]) for h in history] == [
        ("business_description", "Café", "Café de especialidad", "e1")]
    saved = stored(seeded, row)
    assert saved["status"] == "approved" and saved["decided_by"] == "e1" and saved["decided_at"] and saved["error"] is None


def test_approve_topics_replaces_the_list(seeded):
    row = action(seeded, "temas", {"temas": ["Granos", "Métodos"], "motivo": "m"}, {"topics": ["granos"]})
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["topics"] == ["Granos", "Métodos"]
    assert [h["field"] for h in seeded.db.rows("prompt_history")] == ["topics"]


def test_approve_tone_example_appends_to_the_current_ones(seeded):
    row = action(seeded, "ejemplo_tono", {"historias": ["1", "2", "3", "4"], "motivo": "m"}, {"tone_examples": [["a", "b", "c", "d"]]})
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["tone_examples"] == [["a", "b", "c", "d"], ["1", "2", "3", "4"]]
    assert [h["field"] for h in seeded.db.rows("prompt_history")] == ["tone_examples"]


def test_approve_focus_and_format_use_the_prompt_rpc_and_the_format_setter(seeded):
    row = action(seeded, "enfoque_semana", {"enfoque": "Promo del jueves", "formato": FORMAT, "motivo": None},
                 {"weekly_focus": "Viejo", "weekly_format": None})
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["weekly_focus"] == "Promo del jueves" and client_row(seeded)["weekly_format"] == FORMAT
    assert [h["field"] for h in seeded.db.rows("prompt_history")] == ["weekly_focus"]  # the format has no audit, like the manual path


def test_approve_format_only_leaves_the_focus_alone(seeded):
    row = action(seeded, "enfoque_semana", {"enfoque": None, "formato": FORMAT, "motivo": None},
                 {"weekly_focus": "Viejo", "weekly_format": None})
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["weekly_focus"] == "Viejo" and client_row(seeded)["weekly_format"] == FORMAT
    assert seeded.db.rows("prompt_history") == []


@pytest.fixture
def recompose(monkeypatch):
    calls = []

    class Downloaded:
        content = b"original-bytes"
        def raise_for_status(self): pass

    monkeypatch.setattr(stories.requests, "get", lambda url, timeout: Downloaded())

    def fake_edit(config, image_bytes, texto, orden, font_choice=None):
        calls.append((config.nombre_negocio, image_bytes, texto, orden))
        return ImagenEditada(url="https://img/new.png", public_id="pid")
    monkeypatch.setattr(stories.content, "editar_historia", fake_edit)
    return calls


def test_approve_story_text_recomposes_the_image_and_learns_the_correction(seeded, recompose):
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Texto nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    response = approve(seeded, row)
    assert response.status_code == 200
    assert recompose == [("Cafe Sol", b"original-bytes", "Texto nuevo", 2)]
    story = seeded.db.rows("stories")[0]
    assert story["text"] == "Texto nuevo" and story["image_url"] == "https://img/new.png"
    learned = client_row(seeded)["learned_edits"]
    assert learned[0]["antes"] == "Texto viejo" and learned[0]["despues"] == "Texto nuevo"
    assert stored(seeded, row)["status"] == "approved"


def test_double_approve_is_409_and_applies_once(seeded):
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]})
    assert approve(seeded, row).status_code == 200
    again = approve(seeded, row)
    assert again.status_code == 409 and "ya se decidió" in again.json()["detail"]
    assert len(seeded.db.rows("prompt_history")) == 1


def test_a_claimed_action_loses_the_race(seeded):
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]}, status="applying")
    assert approve(seeded, row).status_code == 409
    assert seeded.db.rows("prompt_history") == [] and stored(seeded, row)["status"] == "applying"


def test_stale_value_is_409_and_marks_the_action_stale(seeded):
    row = action(seeded, "descripcion", {"descripcion": "Nueva", "motivo": "m"}, {"business_description": "Café"})
    client_row(seeded)["business_description"] = "Alguien la editó a mano"
    response = approve(seeded, row)
    assert response.status_code == 409 and "cambió" in response.json()["detail"]
    saved = stored(seeded, row)
    assert saved["status"] == "stale" and saved["decided_by"] == "e1" and saved["decided_at"]
    assert client_row(seeded)["business_description"] == "Alguien la editó a mano" and seeded.db.rows("prompt_history") == []


def test_stale_story_text(seeded, recompose):
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    seeded.db.rows("stories")[0]["text"] = "Editada por una persona"
    assert approve(seeded, row).status_code == 409
    assert stored(seeded, row)["status"] == "stale" and recompose == []


def test_story_locked_after_the_proposal_fails_with_detail(seeded, recompose):
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    seeded.db.rows("stories")[0]["estado"] = "publicado"
    response = approve(seeded, row)
    assert response.status_code == 409 and "publicada" in response.json()["detail"]
    saved = stored(seeded, row)
    assert saved["status"] == "failed" and "publicada" in saved["error"] and recompose == []


def test_unexpected_failure_is_failed_not_applying_and_hides_internals(seeded, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cloudinary exploded with secret key abc")
    monkeypatch.setattr(stories.requests, "get", boom)
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    response = approve(seeded, row)
    assert response.status_code == 502 and "secret" not in response.text
    saved = stored(seeded, row)
    assert saved["status"] == "failed" and "secret" not in saved["error"] and saved["decided_by"] == "e1"


def test_row_never_stays_applying_even_on_a_hard_interrupt(seeded, monkeypatch):
    def interrupt(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cm_agent.brand, "apply_prompt_patch", interrupt)
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]})
    with pytest.raises(KeyboardInterrupt):
        approve(seeded, row)
    assert stored(seeded, row)["status"] == "failed"


def test_cross_agency_and_unknown_actions_are_404(seeded):
    foreign = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": []}, conv=FOREIGN_CONV, client=FOREIGN_CLIENT)
    assert approve(seeded, foreign).status_code == 404
    assert seeded.http.post(DISCARD.format(foreign["id"])).status_code == 404
    assert stored(seeded, foreign)["status"] == "pending"
    assert seeded.http.post(APPROVE.format("55555555-5555-4555-8555-999999999999")).status_code == 404
    assert seeded.http.post(APPROVE.format("nope")).status_code == 404
    assert seeded.http.post(DISCARD.format("nope")).status_code == 404


@pytest.mark.parametrize("path", [APPROVE, DISCARD])
def test_flags_off_is_404(seeded, monkeypatch, path):
    row = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": []})
    monkeypatch.setattr(get_settings(), "cm_enabled", False)
    assert seeded.http.post(path.format(row["id"])).status_code == 404
    monkeypatch.setattr(get_settings(), "cm_enabled", True)
    seeded.db.rows("agencies")[0]["cm_enabled"] = False
    assert seeded.http.post(path.format(row["id"])).status_code == 404
    assert stored(seeded, row)["status"] == "pending"


def test_inactive_client_is_409_and_nothing_is_applied(seeded):
    client_row(seeded)["active"] = False
    row = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": ["granos"]})
    response = approve(seeded, row)
    assert response.status_code == 409 and "inactivo" in response.json()["detail"]
    assert stored(seeded, row)["status"] == "pending" and client_row(seeded)["topics"] == ["granos"]


def test_discard_then_double_discard(seeded):
    row = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": ["granos"]})
    response = seeded.http.post(DISCARD.format(row["id"]))
    assert response.status_code == 200 and response.json()["accion"]["estado"] == "discarded"
    saved = stored(seeded, row)
    assert saved["status"] == "discarded" and saved["decided_by"] == "e1" and saved["decided_at"]
    assert seeded.http.post(DISCARD.format(row["id"])).status_code == 409
    assert approve(seeded, row).status_code == 409
    assert client_row(seeded)["topics"] == ["granos"]


def test_an_approved_action_cannot_be_discarded(seeded):
    row = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": ["granos"]})
    assert approve(seeded, row).status_code == 200
    assert seeded.http.post(DISCARD.format(row["id"])).status_code == 409
    assert stored(seeded, row)["status"] == "approved"


def test_get_conversation_returns_the_actions_of_that_conversation_only(seeded):
    mine = action(seeded, "descripcion", {"descripcion": "Nueva", "motivo": "porque sí"}, {"business_description": "Café"})
    action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": []}, conv=FOREIGN_CONV, client=FOREIGN_CLIENT)
    body = seeded.http.get(f"/portal/cm/conversaciones/{CONV}").json()
    assert body["acciones"] == [{
        "id": mine["id"], "tipo": "descripcion", "estado": "pending", "propuesta": {"descripcion": "Nueva"},
        "antes": {"business_description": "Café"}, "motivo": "porque sí", "error": None,
        "created_at": "2026-11-11T12:00:00+00:00", "decided_at": None}]
    assert "tool_use_id" not in str(body)


def test_send_message_returns_the_new_proposals_and_never_writes_the_field(seeded, monkeypatch):
    queue = [
        TurnoCM(texto="", llamadas=[LlamadaHerramienta(id="p1", name="proponer_descripcion", input={"descripcion": "Nueva", "motivo": "m"})],
                stop_reason="tool_use", usage={},
                content_blocks=[{"type": "tool_use", "id": "p1", "name": "proponer_descripcion", "input": {"descripcion": "Nueva", "motivo": "m"}}]),
        TurnoCM(texto="Listo, queda pendiente", llamadas=[], stop_reason="end_turn", usage={},
                content_blocks=[{"type": "text", "text": "Listo, queda pendiente"}]),
    ]
    monkeypatch.setattr(cm_agent.cm, "turno_cm", lambda system, mensajes, version: queue.pop(0))
    response = seeded.http.post(f"/portal/cm/conversaciones/{CONV}/mensajes", json={"texto": "mejorá la descripción"})
    assert response.status_code == 200
    proposals = response.json()["propuestas"]
    assert len(proposals) == 1 and proposals[0]["tipo"] == "descripcion" and proposals[0]["estado"] == "pending"
    assert proposals[0]["antes"] == {"business_description": "Café"}
    assert client_row(seeded)["business_description"] == "Café" and seeded.db.rows("prompt_history") == []
    assert seeded.http.get(f"/portal/cm/conversaciones/{CONV}").json()["acciones"][0]["id"] == proposals[0]["id"]


def test_decision_reaches_the_next_turn(seeded, monkeypatch):
    seen = []

    def turno(system, mensajes, version):
        seen.append(mensajes)
        return TurnoCM(texto="ok", llamadas=[], stop_reason="end_turn", usage={}, content_blocks=[{"type": "text", "text": "ok"}])
    monkeypatch.setattr(cm_agent.cm, "turno_cm", turno)
    seeded.http.post(f"/portal/cm/conversaciones/{CONV}/mensajes", json={"texto": "hola"})
    for row in seeded.db.rows("cm_messages"):
        row["created_at"] = "2026-11-11T12:00:00+00:00"
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]})
    assert approve(seeded, row).status_code == 200
    # make the decision happen after the first user message in the fake's constant clock
    stored(seeded, row)["decided_at"] = "2026-11-11T12:30:00+00:00"
    seeded.http.post(f"/portal/cm/conversaciones/{CONV}/mensajes", json={"texto": "gracias"})
    last_user = seen[-1][-1]["content"][0]["text"]
    assert f"{row['id']}: temas -> approved" in last_user


# ── Fix batch 4b: stuck applying, lost update, enfoque, payload re-validation ──

def _ago(minutes):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_final_decide_failure_keeps_the_original_error(seeded, monkeypatch):
    monkeypatch.setattr(stories.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(cm_agent, "_decide", broken)
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    response = approve(seeded, row)
    assert response.status_code == 502 and "db down" not in response.text


def test_stale_applying_claim_is_recovered_as_failed_on_approve(seeded):
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]}, status="applying")
    row["decided_at"] = _ago(10)
    response = approve(seeded, row)
    assert response.status_code == 409
    saved = stored(seeded, row)
    assert saved["status"] == "failed" and "interrumpió" in saved["error"]
    assert client_row(seeded)["topics"] == ["granos"]


def test_stale_applying_claim_is_recovered_on_discard_and_listing(seeded):
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]}, status="applying")
    row["decided_at"] = _ago(10)
    body = seeded.http.get(f"/portal/cm/conversaciones/{CONV}").json()
    assert body["acciones"][0]["estado"] == "failed" and "interrumpió" in body["acciones"][0]["error"]
    other = action(seeded, "temas", {"temas": ["Dos"], "motivo": "m"}, {"topics": ["granos"]}, status="applying")
    other["decided_at"] = _ago(10)
    assert seeded.http.post(DISCARD.format(other["id"])).status_code == 409
    assert stored(seeded, other)["status"] == "failed"


def test_fresh_applying_claim_is_left_alone(seeded):
    row = action(seeded, "temas", {"temas": ["Uno"], "motivo": "m"}, {"topics": ["granos"]}, status="applying")
    row["decided_at"] = _ago(1)
    assert approve(seeded, row).status_code == 409
    assert stored(seeded, row)["status"] == "applying"


def test_tone_cap_is_rechecked_at_apply_time(seeded):
    six = [[str(i)] * 4 for i in range(6)]
    client_row(seeded)["tone_examples"] = six
    row = action(seeded, "ejemplo_tono", {"historias": ["1", "2", "3", "4"], "motivo": "m"}, {"tone_examples": six})
    response = approve(seeded, row)
    assert response.status_code == 409
    assert stored(seeded, row)["status"] == "failed" and client_row(seeded)["tone_examples"] == six


def test_story_ownership_is_rechecked_right_before_the_write(seeded, monkeypatch, recompose):
    row = action(seeded, "texto_historia", {"historia_id": STORY, "texto": "Nuevo", "motivo": "m"}, {"text": "Texto viejo"})
    real = cm_agent._current_value

    def moved(db, action_row, agency_id):
        result = real(db, action_row, agency_id)
        seeded.db.rows("stories")[0]["client_id"] = FOREIGN_CLIENT
        return result
    monkeypatch.setattr(cm_agent, "_current_value", moved)
    assert approve(seeded, row).status_code == 409
    assert stored(seeded, row)["status"] == "failed" and recompose == []
    assert seeded.db.rows("stories")[0]["text"] == "Texto viejo"


def test_format_only_proposal_ignores_a_changed_focus(seeded):
    row = action(seeded, "enfoque_semana", {"enfoque": None, "formato": FORMAT, "motivo": None},
                 {"weekly_focus": "Viejo", "weekly_format": None})
    client_row(seeded)["weekly_focus"] = "Cambiado a mano"
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["weekly_focus"] == "Cambiado a mano" and client_row(seeded)["weekly_format"] == FORMAT


def test_focus_only_proposal_ignores_a_changed_format(seeded):
    row = action(seeded, "enfoque_semana", {"enfoque": "Nuevo", "formato": None, "motivo": None},
                 {"weekly_focus": "Viejo", "weekly_format": None})
    client_row(seeded)["weekly_format"] = FORMAT
    assert approve(seeded, row).status_code == 200
    assert client_row(seeded)["weekly_focus"] == "Nuevo" and client_row(seeded)["weekly_format"] == FORMAT


def test_second_step_failure_says_what_was_applied(seeded, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret")
    monkeypatch.setattr(cm_agent.brand, "set_weekly_format", boom)
    row = action(seeded, "enfoque_semana", {"enfoque": "Nuevo", "formato": FORMAT, "motivo": None},
                 {"weekly_focus": "Viejo", "weekly_format": None})
    response = approve(seeded, row)
    assert response.status_code in (409, 502) and "secret" not in response.text
    saved = stored(seeded, row)
    assert saved["status"] == "failed" and "enfoque" in saved["error"].lower() and "formato" in saved["error"].lower()
    assert client_row(seeded)["weekly_focus"] == "Nuevo"


@pytest.mark.parametrize("kind,payload,before", [
    ("temas", {"temas": [f"t{i}" for i in range(20)], "motivo": "m"}, {"topics": ["granos"]}),
    ("descripcion", {"descripcion": "x" * 501, "motivo": "m"}, {"business_description": "Café"}),
    ("ejemplo_tono", {"historias": ["a"], "motivo": "m"}, {"tone_examples": [["a", "b", "c", "d"]]}),
    ("enfoque_semana", {"enfoque": None, "formato": "no-existe", "motivo": None}, {"weekly_focus": "Viejo", "weekly_format": None}),
])
def test_tampered_payload_fails_without_reaching_the_rpc(seeded, monkeypatch, kind, payload, before):
    def no_write(*a, **k):
        raise AssertionError("must not write")
    monkeypatch.setattr(cm_agent.brand, "apply_prompt_patch", no_write)
    monkeypatch.setattr(cm_agent.brand, "set_weekly_format", no_write)
    row = action(seeded, kind, payload, before)
    response = approve(seeded, row)
    assert response.status_code in (409, 422)
    saved = stored(seeded, row)
    assert saved["status"] == "failed" and saved["error"]


def test_action_whose_client_differs_from_its_conversation_is_404(seeded):
    row = action(seeded, "temas", {"temas": ["x"], "motivo": "m"}, {"topics": ["granos"]}, conv=CONV, client=FOREIGN_CLIENT)
    assert approve(seeded, row).status_code == 404
    assert seeded.http.post(DISCARD.format(row["id"])).status_code == 404
    assert stored(seeded, row)["status"] == "pending"
