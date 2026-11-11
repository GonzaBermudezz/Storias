import pytest

from app.engine.schemas import ImagenEditada
from app.services import stories
from app.services.errors import ServiceError
from fake_db import FakeDB

STORY = {"id": "s1", "client_id": "c1", "order": 2, "text": "Antes", "image_url": "u",
         "image_original_url": "http://img/o.png", "estado": "pendiente", "font_choice": None}
CLIENT = {"id": "c1", "agency_id": "a1", "name": "Cafe", "business_description": "d", "learned_edits": []}


class FakeResponse:
    content = b"bytes"

    def raise_for_status(self):
        pass


@pytest.fixture
def engine(monkeypatch):
    calls = []
    monkeypatch.setattr(stories.requests, "get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(stories.content, "editar_historia", lambda config, image, text, order, font_choice=None:
                        calls.append((text, order, font_choice)) or ImagenEditada(url="http://new", public_id="p"))
    return calls


def test_update_story_text_recomposes_saves_and_remembers(engine):
    db = FakeDB([dict(STORY), dict(CLIENT), [{**STORY, "text": "Después"}], None])
    result = stories.update_story_text(db, "s1", "a1", "Después")
    assert engine == [("Después", 2, None)]
    assert result["text"] == "Después"
    updates = db.ops("update")
    assert updates[0][2] == {"text": "Después", "image_url": "http://new"}
    assert updates[1][2]["learned_edits"][0]["antes"] == "Antes"


def test_update_story_text_errors(engine):
    with pytest.raises(ServiceError) as info:
        stories.update_story_text(FakeDB([None]), "s1", "a1", "x")
    assert info.value.status_code == 404
    with pytest.raises(ServiceError) as info:
        stories.update_story_text(FakeDB([dict(STORY), {**CLIENT, "agency_id": "o"}]), "s1", "a1", "x")
    assert info.value.status_code == 403
    with pytest.raises(ServiceError) as info:
        stories.update_story_text(FakeDB([{**STORY, "estado": "publicado"}, dict(CLIENT)]), "s1", "a1", "x")
    assert info.value.status_code == 409
    with pytest.raises(ServiceError) as info:
        stories.update_story_text(FakeDB([{**STORY, "image_original_url": None}, dict(CLIENT)]), "s1", "a1", "x")
    assert info.value.status_code == 422
    assert engine == []


def test_remember_correction_skips_blank_before_and_cosmetic_changes():
    db = FakeDB([])
    stories.remember_correction(db, dict(CLIENT), "", "nuevo")
    stories.remember_correction(db, dict(CLIENT), "Hola  Mundo", "hola mundo")
    assert db.calls == []


def test_remember_correction_replaces_same_before_and_keeps_eight():
    old = [{"id": str(i), "antes": f"a{i}", "despues": "d"} for i in range(8)]
    db = FakeDB([[]])
    stories.remember_correction(db, {"id": "c1", "learned_edits": old}, "A3", "otro")
    saved = db.ops("update")[0][2]["learned_edits"]
    assert len(saved) == 8 and saved[0]["despues"] == "otro"
    assert [item["antes"] for item in saved].count("a3") == 0


def test_remember_correction_never_raises():
    class Boom(FakeDB):
        def table(self, name):
            raise RuntimeError("db down")

    stories.remember_correction(Boom([]), dict(CLIENT), "a", "b")


def test_list_week_hides_cancelled_and_generating():
    groups = [{"id": "g", "stories": [{"id": 1, "estado": "cancelada"}, {"id": 2, "estado": "generando"},
                                       {"id": 3, "estado": "pendiente"}]}]
    result = stories.list_week(FakeDB([dict(CLIENT), groups]), "c1", "a1")
    assert [story["id"] for story in result[0]["stories"]] == [3]
    assert stories.LOCKED_STATES == {"generando", "publicando", "publicado", "cancelada"}
