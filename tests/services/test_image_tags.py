import io
from types import SimpleNamespace

from PIL import Image

from app.services import image_tags

# Referencia a la función real, tomada al importar el módulo (antes de que el
# autouse de tests/conftest.py la reemplace por un stub en cada test).
_REAL_CLASSIFY = image_tags.classify_image


def test_parse_tag_accepts_valid_json_and_normalizes_the_scene():
    assert image_tags.parse_tag('{"persona": true, "escena": "Producto"}') == {"persona": True, "escena": "producto", "producto": ""}
    assert image_tags.parse_tag('Acá va: {"persona": false, "escena": "inventada"} listo') == {"persona": False, "escena": "otro", "producto": ""}


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
    monkeypatch.delenv("IMAGE_TAG_MODEL", raising=False)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            assert kwargs["model"] == image_tags.CLAUDE_MODEL
            return SimpleNamespace(content=[SimpleNamespace(text='{"persona": false, "escena": "detalle"}')])

    monkeypatch.setattr(image_tags.anthropic, "Anthropic", FakeClient)
    assert _REAL_CLASSIFY(data) == {"persona": False, "escena": "detalle", "producto": ""}

    def broken(*args, **kwargs):
        raise RuntimeError("caído")

    monkeypatch.setattr(image_tags.anthropic, "Anthropic", broken)
    assert _REAL_CLASSIFY(data) is None

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert _REAL_CLASSIFY(data) is None


# ---------------------------------------------------------------------------
# a79: producto
# ---------------------------------------------------------------------------
def test_parse_tag_devuelve_el_producto_recortado():
    largo = "reloj " + "x" * 100
    tag = image_tags.parse_tag('{"persona": false, "escena": "producto", "producto": "  %s  "}' % largo)
    assert tag["producto"] == largo[:60]
    assert len(tag["producto"]) == 60
    assert image_tags.parse_tag(
        '{"persona": false, "escena": "producto", "producto": " drone DJI "}')["producto"] == "drone DJI"


def test_parse_tag_sin_producto_o_no_texto_queda_vacio():
    assert image_tags.parse_tag('{"persona": true, "escena": "ambiente"}')["producto"] == ""
    assert image_tags.parse_tag('{"persona": true, "escena": "ambiente", "producto": 42}')["producto"] == ""
    assert image_tags.parse_tag('{"persona": true, "escena": "ambiente", "producto": null}')["producto"] == ""


def test_parse_tag_con_persona_invalida_sigue_siendo_none():
    assert image_tags.parse_tag('{"persona": "si", "escena": "detalle", "producto": "drone"}') is None


def test_prompt_pide_el_producto():
    assert '"producto"' in image_tags._PROMPT


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _fake_anthropic(monkeypatch, seen: dict):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(
                text='{"persona": false, "escena": "producto", "producto": "drone DJI"}')])

    monkeypatch.setattr(image_tags.anthropic, "Anthropic", FakeClient)


def test_classify_image_usa_image_tag_model_si_esta_definido(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE_TAG_MODEL", "modelo-barato")
    seen = {}
    _fake_anthropic(monkeypatch, seen)

    assert _REAL_CLASSIFY(_jpeg())["producto"] == "drone DJI"
    assert seen["model"] == "modelo-barato"
    assert seen["max_tokens"] == 100


def test_classify_image_sin_image_tag_model_usa_claude_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("IMAGE_TAG_MODEL", raising=False)
    seen = {}
    _fake_anthropic(monkeypatch, seen)

    _REAL_CLASSIFY(_jpeg())
    assert seen["model"] == image_tags.CLAUDE_MODEL
