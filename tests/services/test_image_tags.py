import io
from types import SimpleNamespace

from PIL import Image

from app.services import image_tags

# Referencia a la función real, tomada al importar el módulo (antes de que el
# autouse de tests/conftest.py la reemplace por un stub en cada test).
_REAL_CLASSIFY = image_tags.classify_image


def test_parse_tag_accepts_valid_json_and_normalizes_the_scene():
    assert image_tags.parse_tag('{"persona": true, "escena": "Producto"}') == {"persona": True, "escena": "producto"}
    assert image_tags.parse_tag('Acá va: {"persona": false, "escena": "inventada"} listo') == {"persona": False, "escena": "otro"}


def test_parse_tag_rejects_garbage():
    assert image_tags.parse_tag(None) is None
    assert image_tags.parse_tag("no hay json") is None
    assert image_tags.parse_tag('{"persona": "si", "escena": "detalle"}') is None
    assert image_tags.parse_tag("{rota") is None


def test_classify_many_keeps_only_the_photos_that_could_be_classified(monkeypatch):
    results = {b"a": {"persona": True, "escena": "ambiente"}, b"b": None}
    monkeypatch.setattr(image_tags, "classify_image", lambda data: results[data])
    assert image_tags.classify_many([("f1", "a.jpg", b"a"), ("f2", "b.jpg", b"b")]) == {
        "f1": {"persona": True, "escena": "ambiente"}}
    assert image_tags.classify_many([]) == {}


def test_classify_image_uses_claude_vision_and_survives_failures(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="JPEG")
    data = buffer.getvalue()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            assert kwargs["model"] == image_tags.CLAUDE_MODEL
            return SimpleNamespace(content=[SimpleNamespace(text='{"persona": false, "escena": "detalle"}')])

    monkeypatch.setattr(image_tags.anthropic, "Anthropic", FakeClient)
    assert _REAL_CLASSIFY(data) == {"persona": False, "escena": "detalle"}

    def broken(*args, **kwargs):
        raise RuntimeError("caído")

    monkeypatch.setattr(image_tags.anthropic, "Anthropic", broken)
    assert _REAL_CLASSIFY(data) is None

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert _REAL_CLASSIFY(data) is None
