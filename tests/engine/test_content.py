"""Tests del motor de contenido multi-cliente.

Los fixtures reproducen los valores reales de CUAN (personaje, 6 ejemplos
de tono, 10 temas) para documentar que la migracion preserva el
comportamiento original. La API de Claude y Cloudinary se mockean: no se
gasta credito real.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from PIL import Image

from app.engine import content
from app.engine.content import (
    CLAUDE_MODEL,
    TEMA_FALLBACK,
    _armar_prompt,
    _elegir_tema,
)
from app.engine.exceptions import EngineError, MetaPublishError
from app.engine.formatos import FORMATOS
from app.engine.schemas import (
    ClientContentConfig,
    HiloGenerado,
    ImagenCandidata,
    ResultadoPublicacion,
)

# ---------------------------------------------------------------------------
# Fixtures: valores reales de CUAN del main.py original
# ---------------------------------------------------------------------------

BUSINESS_DESCRIPTION_CUAN = (
    "Sos el copywriter de CUAN, un estudio que diseña cocinas "
    "a medida para personas que están construyendo o remodelando "
    "su casa. Tu público NO son arquitectos ni diseñadores: son "
    "personas comunes armando su casa, muchas veces por primera vez."
)

TONE_EXAMPLES_CUAN = [
    [
        "El error más caro de una cocina no es la mesada.",
        "Es construir algo que después no funciona.",
        "Por eso diseñamos todo en 3D antes de la obra.",
        "¿Pensás renovar tu cocina? Escribí COCINA.",
    ],
    [
        "¿Tu cocina se siente chica?",
        "Muchas veces no faltan metros.",
        "Falta una mejor distribución.",
        "Mandá DISEÑO y te mostramos posibilidades.",
    ],
    [
        "La mayoría elige materiales demasiado pronto.",
        "Y se olvida de lo más importante.",
        "La distribución.",
        "Escribí PLAN y te contamos cómo trabajamos.",
    ],
    [
        "¿Querés una isla?",
        "No siempre es la mejor solución.",
        "Cada cocina necesita una estrategia distinta.",
        "Mandá PROYECTO y lo vemos juntos.",
    ],
    [
        "Tu cocina te va a acompañar años.",
        "No diseñes a prueba y error.",
        "Visualizala completa antes de construir.",
        "Escribí COCINA para agendar una reunión.",
    ],
    [
        "Una decisión puede arruinar toda una cocina.",
        "Y la mayoría la toma demasiado rápido.",
        "Te contamos cuál es en la reunión.",
        "Mandá QUIERO y coordinamos.",
    ],
]

TOPICS_CUAN = [
    "distribucion del espacio en la cocina",
    "errores comunes al diseñar una cocina",
    "diseño 3D antes de la obra",
    "islas de cocina: cuando conviene y cuando no",
    "eleccion de materiales para cocina",
    "mesadas y superficies",
    "guardado y almacenamiento en la cocina",
    "errores caros al renovar la cocina",
    "planificar la cocina antes de construir",
    "decisiones que arruinan el diseño de una cocina",
]

# Respuesta cruda de Claude con un emoji a limpiar en la historia 4.
RAW_HILO_CUAN = (
    "El error más caro de una cocina no es la mesada. ||| "
    "Es construir algo que después no funciona. ||| "
    "Por eso diseñamos todo en 3D antes de la obra. ||| "
    "Escribí COCINA 🎉 y coordinamos."
)


def config_cuan(**overrides) -> ClientContentConfig:
    base = {
        "client_id": "cuan-cliente",
        "nombre_negocio": "CUAN",
        "business_description": BUSINESS_DESCRIPTION_CUAN,
        "tone_examples": TONE_EXAMPLES_CUAN,
        "topics": TOPICS_CUAN,
        "prob_link": 0.0,
        # logo_url en None por defecto: los tests no hacen red real.
    }
    base.update(overrides)
    return ClientContentConfig(**base)


def imagen_falsa(w: int = 400, h: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def imagen_candidata(i: int) -> ImagenCandidata:
    return ImagenCandidata(
        drive_file_id=f"drive_{i}",
        drive_file_name=f"img_{i}.jpg",
        image_bytes=imagen_falsa(),
    )


class FakeAnthropicResponse:
    def __init__(self, texto: str = RAW_HILO_CUAN):
        self.content = [SimpleNamespace(text=texto)]


class FakeAnthropicClient:
    def __init__(self, *args, **kwargs):
        self.messages = mock.MagicMock()
        self.messages.create.return_value = FakeAnthropicResponse()

    def crear_para(self, texto: str) -> "FakeAnthropicClient":
        self.messages.create.return_value = FakeAnthropicResponse(texto)
        return self


@pytest.fixture
def mock_servicios(monkeypatch):
    """Mockea Claude y Cloudinary, y setea las env vars que el engine lee."""
    fake_client = FakeAnthropicClient()

    def fake_upload(image_bytes, public_id=None, resource_type=None):
        return {"secure_url": f"https://res.cloudinary.com/storias/{public_id}"}

    fake_upload = mock.MagicMock(side_effect=fake_upload)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "test-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "test-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "test-secret")
    monkeypatch.setattr(content.anthropic, "Anthropic", lambda *a, **k: fake_client)
    monkeypatch.setattr(content.cloudinary.uploader, "upload", fake_upload)
    return fake_client


# ---------------------------------------------------------------------------
# Identidad / migracion
# ---------------------------------------------------------------------------

def test_modelo_de_claude_es_constante_corregida():
    assert CLAUDE_MODEL == "claude-sonnet-5"
    assert CLAUDE_MODEL != "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# generar_hilo
# ---------------------------------------------------------------------------

def test_generar_hilo_devuelve_hilo_con_4_historias_y_4_mas_4_urls(mock_servicios):
    config = config_cuan()
    imagenes = [imagen_candidata(i) for i in range(1, 5)]

    resultado: HiloGenerado = content.generar_hilo(config, imagenes)

    assert isinstance(resultado, HiloGenerado)
    assert len(resultado.historias) == 4
    assert len(resultado.imagenes_originales_url) == 4
    assert len(resultado.imagenes_editadas_url) == 4
    assert all(u.startswith("https://") for u in resultado.imagenes_originales_url)
    assert all(u.startswith("https://") for u in resultado.imagenes_editadas_url)
    assert resultado.drive_file_ids_usados == ["drive_1", "drive_2", "drive_3", "drive_4"]
    # El emoji de la historia 4 se limpio.
    assert "🎉" not in " ".join(resultado.historias)
    # Se subio 4 veces SIN texto y 4 veces editada.
    assert mock_servicios.messages.create.call_count == 1


def test_generar_hilo_con_3_imagenes_levanta_engineerror(mock_servicios):
    config = config_cuan()
    imagenes = [imagen_candidata(i) for i in range(1, 4)]

    with pytest.raises(EngineError, match="exactamente 4"):
        content.generar_hilo(config, imagenes)


def test_generar_hilo_con_0_imagenes_levanta_engineerror(mock_servicios):
    with pytest.raises(EngineError, match="exactamente 4"):
        content.generar_hilo(config_cuan(), [])


def test_error_de_claude_se_envuelve_en_claudegenerationerror(mock_servicios):
    mock_servicios.messages.create.side_effect = RuntimeError("boom de anthropic")

    with pytest.raises(content.ClaudeGenerationError, match="boom de anthropic"):
        content.generar_hilo(config_cuan(), [imagen_candidata(i) for i in range(1, 5)])


# ---------------------------------------------------------------------------
# Prompt: _armar_prompt y eleccion de tema
# ---------------------------------------------------------------------------

def test_prompt_con_weekly_focus_contiene_el_enfoque_obligatorio():
    config = config_cuan(weekly_focus="destacar la venta que cerramos el martes")

    prompt = _armar_prompt(config)

    assert "destacar la venta que cerramos el martes" in prompt
    assert "(obligatorio esta semana)" in prompt
    # No se eligio ningun tema random de la lista.
    assert not any(t in prompt for t in TOPICS_CUAN)


def test_prompt_sin_weekly_focus_usa_un_tema_random_de_topics():
    config = config_cuan()

    prompt = _armar_prompt(config)

    assert any(t in prompt for t in TOPICS_CUAN)


def test_prompt_sin_topics_ni_weekly_focus_usa_fallback_generico():
    config = config_cuan(topics=None)

    prompt = _armar_prompt(config)

    assert TEMA_FALLBACK in prompt
    # El fallback NO es un tema de cocina.
    assert "cocina" not in TEMA_FALLBACK


def test_elegir_tema_prioriza_weekly_focus():
    config = config_cuan(weekly_focus="promocion de la semana")
    assert _elegir_tema(config) == "promocion de la semana"


def test_elegir_tema_sin_weekly_focus_devuelve_topics():
    config = config_cuan()
    assert _elegir_tema(config) in TOPICS_CUAN


def test_elegir_tema_sin_nada_devuelve_fallback():
    assert _elegir_tema(config_cuan(topics=None)) == TEMA_FALLBACK


def test_elegir_tema_usa_el_tema_elegido_antes_que_el_random(monkeypatch):
    def no_random(_):
        raise AssertionError("random.choice no debería llamarse con tema_elegido")

    monkeypatch.setattr(content.random, "choice", no_random)
    config = config_cuan(tema_elegido="Garmin para running")

    assert all(_elegir_tema(config) == "Garmin para running" for _ in range(20))


def test_elegir_tema_weekly_focus_gana_al_tema_elegido():
    config = config_cuan(weekly_focus="cerramos una venta", tema_elegido="Garmin para running")
    assert _elegir_tema(config) == "cerramos una venta"


def test_elegir_tema_sin_tema_elegido_sigue_igual():
    assert _elegir_tema(config_cuan(tema_elegido=None)) in TOPICS_CUAN
    assert _elegir_tema(config_cuan(tema_elegido=None, topics=None)) == TEMA_FALLBACK


def test_prompt_con_tema_elegido_no_es_obligatorio():
    prompt = _armar_prompt(config_cuan(tema_elegido="Garmin para running"))

    assert "sobre este tema: Garmin para running" in prompt
    assert "(obligatorio esta semana)" not in prompt


def test_prompt_generalizado_no_menciona_cocinas_por_defecto():
    prompt = _armar_prompt(config_cuan(weekly_focus="campaña de mes de aniversario"))
    assert "el diferencial de CUAN" in prompt  # por config.nombre_negocio
    assert "arquitectura" not in prompt.lower()


# ---------------------------------------------------------------------------
# Formato de la semana (a78)
# ---------------------------------------------------------------------------
_ARCO_DE_HOY = (
    "- Historia 1: una afirmación o pregunta que genera intriga, conectada al tema (máx 10 palabras)\n"
    "- Historia 2: desarrolla la tensión o el problema (máx 8 palabras)\n"
    "- Historia 3: el diferencial de CUAN, la solución (máx 8 palabras)\n"
    "- Historia 4: CTA con una palabra clave en mayúsculas para escribir y agendar (máx 10 palabras)\n\n"
)


def test_arco_por_defecto_no_cambia():
    prompt = _armar_prompt(config_cuan(weekly_format=None))

    assert _ARCO_DE_HOY in prompt
    assert "- Cada historia es UNA sola idea, corta, con gancho\n" + _ARCO_DE_HOY + "La imagen adjunta" in prompt
    assert "Formato de esta semana" not in prompt


@pytest.mark.parametrize("clave", sorted(FORMATOS))
def test_cada_formato_reemplaza_el_arco(clave):
    formato = FORMATOS[clave]

    prompt = _armar_prompt(config_cuan(weekly_format=clave))

    assert f"Formato de esta semana: {formato['label']}" in prompt
    for i, texto in enumerate(formato["arco"], 1):
        assert f"- Historia {i}: {texto.format(negocio='CUAN')}\n" in prompt
    assert "{negocio}" not in prompt
    assert "desarrolla la tensión o el problema" not in prompt


def test_formato_desconocido_usa_el_arco_por_defecto():
    prompt = _armar_prompt(config_cuan(weekly_format="inventado"))

    assert _ARCO_DE_HOY in prompt
    assert "Formato de esta semana" not in prompt


def test_formatos_estan_bien_formados():
    assert FORMATOS
    for clave, formato in FORMATOS.items():
        assert formato["label"].strip(), clave
        assert formato["hint"].strip(), clave
        assert len(formato["arco"]) == 4, clave
        for texto in formato["arco"]:
            assert texto.strip() and "máx" in texto, clave


# ---------------------------------------------------------------------------
# editar_historia y generar_texto_de_prueba
# ---------------------------------------------------------------------------

def test_editar_historia_devuelve_asset_de_cloudinary(mock_servicios):
    config = config_cuan()

    imagen_editada = content.editar_historia(config, imagen_falsa(), "Texto nuevo", num_historia=3)

    assert imagen_editada.url.startswith("https://")
    assert "re_edit_3" in imagen_editada.url
    assert "re_edit_3" in imagen_editada.public_id


def test_editar_historia_uses_distinct_cloudinary_public_ids(mock_servicios):
    config = config_cuan()

    first = content.editar_historia(config, imagen_falsa(), "Uno", num_historia=1)
    second = content.editar_historia(config, imagen_falsa(), "Dos", num_historia=1)

    assert first.public_id != second.public_id


def test_editar_historia_usa_el_font_choice_pasado_por_parametro(mock_servicios, monkeypatch):
    # Un override puntual por historia (el empleado elige otra tipografia
    # solo para esta Story desde el editor) debe pisar la del cliente.
    config = config_cuan(font_choice="dm_sans")
    captured = {}
    monkeypatch.setattr(content, "componer_historia",
        lambda *a, **k: captured.update(k) or imagen_falsa())

    content.editar_historia(config, imagen_falsa(), "Texto", num_historia=1, font_choice="poppins")

    assert captured["font_choice"] == "poppins"


def test_editar_historia_sin_override_usa_la_tipografia_del_cliente(mock_servicios, monkeypatch):
    config = config_cuan(font_choice="dm_sans")
    captured = {}
    monkeypatch.setattr(content, "componer_historia",
        lambda *a, **k: captured.update(k) or imagen_falsa())

    content.editar_historia(config, imagen_falsa(), "Texto", num_historia=1)

    assert captured["font_choice"] == "dm_sans"


def test_generar_texto_de_prueba_devuelve_solo_texto(mock_servicios):
    config = config_cuan()

    historias = content.generar_texto_de_prueba(config, imagen_candidata(1))

    assert isinstance(historias, list)
    assert len(historias) == 4
    # No sube nada a Cloudinary.
    assert content.cloudinary.uploader.upload.call_count == 0


# ---------------------------------------------------------------------------
# generar_texto_para_imagen (subida manual: una sola historia, imagen real)
# ---------------------------------------------------------------------------

def test_generar_texto_para_imagen_devuelve_un_solo_texto_limpio(mock_servicios):
    mock_servicios.crear_para("Así se ve tu cocina en 3D antes de la obra 🎉")
    config = config_cuan()

    texto = content.generar_texto_para_imagen(config, imagen_falsa())

    assert isinstance(texto, str)
    assert texto == "Así se ve tu cocina en 3D antes de la obra"
    # No sube nada a Cloudinary: solo genera texto.
    assert content.cloudinary.uploader.upload.call_count == 0
    assert mock_servicios.messages.create.call_count == 1


def test_generar_texto_para_imagen_usa_pocos_tokens_para_una_sola_historia(mock_servicios):
    mock_servicios.crear_para("Una historia corta.")
    content.generar_texto_para_imagen(config_cuan(), imagen_falsa())

    _, kwargs = mock_servicios.messages.create.call_args
    assert kwargs["max_tokens"] == 60


def test_generar_texto_para_imagen_sin_texto_levanta_claudegenerationerror(mock_servicios):
    mock_servicios.crear_para("   ")

    with pytest.raises(content.ClaudeGenerationError, match="no devolvió texto"):
        content.generar_texto_para_imagen(config_cuan(), imagen_falsa())


def test_generar_texto_para_imagen_descarta_todo_despues_del_primer_separador(mock_servicios):
    # Si Claude ignora "una sola historia" y devuelve un hilo de 4 con |||
    # (el formato del prompt semanal), nos quedamos solo con la primera.
    mock_servicios.crear_para(
        "Esa esquina vacía puede ser un rincón de lectura. ||| "
        "Todo depende de cómo pienses el espacio. ||| "
        "Nosotros lo vemos antes de construirlo. ||| "
        "Escribí ESPACIO y te mostramos cómo."
    )
    texto = content.generar_texto_para_imagen(config_cuan(), imagen_falsa())
    assert texto == "Esa esquina vacía puede ser un rincón de lectura."


def test_generar_texto_para_imagen_descarta_lineas_extra(mock_servicios):
    mock_servicios.crear_para("Primera línea.\nSegunda línea que no debería aparecer.")
    texto = content.generar_texto_para_imagen(config_cuan(), imagen_falsa())
    assert texto == "Primera línea."


def test_generar_texto_para_imagen_corta_textos_demasiado_largos(mock_servicios):
    mock_servicios.crear_para("palabra " * 40)
    texto = content.generar_texto_para_imagen(config_cuan(), imagen_falsa())
    assert len(texto) <= 91  # cap + "…"
    assert texto.endswith("…")


def test_generar_texto_para_imagen_envuelve_error_de_claude(mock_servicios):
    mock_servicios.messages.create.side_effect = RuntimeError("boom de anthropic")

    with pytest.raises(content.ClaudeGenerationError, match="boom de anthropic"):
        content.generar_texto_para_imagen(config_cuan(), imagen_falsa())


# ---------------------------------------------------------------------------
# publicar_historia (Meta Graph API)
# ---------------------------------------------------------------------------

def _fake_requests_post(monkeypatch, respuestas):
    def fake_post(url, data=None, **_kw):
        return respuestas.pop(0)

    monkeypatch.setattr(content.requests, "post", fake_post)


def _fake_requests_get_finished(monkeypatch):
    """Default GET mock: container is ready on the first poll — existing
    tests that don't care about polling don't need to think about it."""
    def fake_get(url, params=None, **_kw):
        return SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"status_code": "FINISHED"},
        )

    monkeypatch.setattr(content.requests, "get", fake_get)
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)


def test_publicar_historia_ok_devuelve_resultado_publicacion(monkeypatch):
    class RespOk:
        ok = True

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "POST_456"}

    media_resp = RespOk()
    media_resp.json = lambda: {"id": "MEDIA_123"}
    pub_resp = RespOk()

    _fake_requests_post(monkeypatch, [media_resp, pub_resp])
    _fake_requests_get_finished(monkeypatch)

    resultado = content.publicar_historia(
        image_url="https://res.cloudinary.com/x.jpg",
        instagram_account_id="ig_acct",
        meta_access_token="token",
        agregar_cta=True,
        calendly_link="https://calendly.com/cuan",
    )

    assert isinstance(resultado, ResultadoPublicacion)
    assert resultado.ok is True
    assert resultado.ig_media_id == "POST_456"


def test_publicar_historia_ok_manda_story_cta_solo_con_link(monkeypatch):
    payloads = []

    def fake_post(url, data=None, **_kw):
        payloads.append(data)
        return SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"id": "X"},
        )

    monkeypatch.setattr(content.requests, "post", fake_post)
    _fake_requests_get_finished(monkeypatch)

    content.publicar_historia("url", "acct", "tok", agregar_cta=True, calendly_link="https://cal.com/x")
    assert "story_cta" in payloads[0]

    content.publicar_historia("url", "acct", "tok", agregar_cta=True, calendly_link=None)
    assert "story_cta" not in payloads[2]


def test_publicar_historia_fallo_http_envuelto_en_metapublisheerror(monkeypatch):
    class RespError:
        ok = False

        def raise_for_status(self):
            raise RuntimeError("HTTP 400 desde Meta")

    monkeypatch.setattr(content.requests, "post", lambda *a, **k: RespError())
    _fake_requests_get_finished(monkeypatch)

    with pytest.raises(MetaPublishError, match="HTTP 400"):
        content.publicar_historia("url", "acct", "tok", False, None)


def test_publicar_historia_incluye_el_cuerpo_de_la_respuesta_de_meta_en_el_error(monkeypatch):
    class FakeMetaResponse:
        status_code = 400
        text = '{"error": {"message": "Media type STORIES no soportado para esta cuenta", "code": 100}}'

    class RespError:
        ok = False

        def raise_for_status(self):
            http_err = content.requests.exceptions.HTTPError(
                "400 Client Error: Bad Request for url: https://graph.facebook.com/v21.0/x/media"
            )
            http_err.response = FakeMetaResponse()
            raise http_err

    monkeypatch.setattr(content.requests, "post", lambda *a, **k: RespError())
    _fake_requests_get_finished(monkeypatch)

    with pytest.raises(MetaPublishError, match="Media type STORIES no soportado"):
        content.publicar_historia("url", "acct", "tok", False, None)


def test_publicar_historia_redacta_el_token_antes_de_truncar_el_error_de_meta(monkeypatch):
    token = "SECRET_ACCESS_TOKEN_12345"

    class FakeMetaResponse:
        text = ("x" * 495) + token + "tail"

    class RespError:
        def raise_for_status(self):
            http_err = content.requests.exceptions.HTTPError("400 Client Error")
            http_err.response = FakeMetaResponse()
            raise http_err

    monkeypatch.setattr(content.requests, "post", lambda *a, **k: RespError())
    _fake_requests_get_finished(monkeypatch)

    with pytest.raises(MetaPublishError) as exc_info:
        content.publicar_historia("url", "acct", token, False, None)

    assert token not in str(exc_info.value)
    assert token[:5] not in str(exc_info.value)
    assert "[reda" in str(exc_info.value)


def test_publicar_historia_espera_varios_intentos_antes_de_finished(monkeypatch):
    estados = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    get_calls = []

    def fake_get(url, params=None, **_kw):
        get_calls.append(params)
        return SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"status_code": next(estados)},
        )

    monkeypatch.setattr(content.requests, "get", fake_get)
    sleeps = []
    monkeypatch.setattr(content.time, "sleep", lambda seconds: sleeps.append(seconds))
    _fake_requests_post(monkeypatch, [
        SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"id": "MEDIA_123"},
        ),
        SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"id": "POST_456"},
        ),
    ])

    resultado = content.publicar_historia("url", "acct", "tok", False, None)

    assert resultado.ok is True
    assert len(get_calls) == 3
    assert sleeps == [content.STATUS_POLL_INTERVAL_SECONDS] * 2


def test_publicar_historia_container_en_error_no_publica(monkeypatch):
    monkeypatch.setattr(content.requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"status_code": "ERROR"},
    ))
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)
    post_calls = []
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: (
        post_calls.append(url),
        SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"id": "MEDIA_123"},
        ),
    )[1])

    with pytest.raises(MetaPublishError, match="ERROR"):
        content.publicar_historia("url", "acct", "tok", False, None)

    assert len(post_calls) == 1


def test_publicar_historia_container_expirado_no_publica(monkeypatch):
    monkeypatch.setattr(content.requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"status_code": "EXPIRED"},
    ))
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"id": "MEDIA_123"},
    ))

    with pytest.raises(MetaPublishError, match="EXPIRED"):
        content.publicar_historia("url", "acct", "tok", False, None)


def test_publicar_historia_consulta_el_estado_del_container_por_su_id(monkeypatch):
    gets = []

    def fake_get(url, params=None, **_kw):
        gets.append(url)
        return SimpleNamespace(
            ok=True,
            raise_for_status=lambda: None,
            json=lambda: {"status_code": "FINISHED"},
        )

    monkeypatch.setattr(content.requests, "get", fake_get)
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"id": "MEDIA_123"},
    ))

    content.publicar_historia("url", "ACCT_999", "tok", False, None)

    assert gets == [f"{content.GRAPH_API_BASE}/MEDIA_123"]
    assert "ACCT_999" not in gets[0]



def test_publicar_historia_timeout_de_polling_tira_metapublisheerror(monkeypatch):
    monkeypatch.setattr(content.requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"status_code": "IN_PROGRESS"},
    ))
    sleeps = []
    monkeypatch.setattr(content.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"id": "MEDIA_123"},
    ))

    with pytest.raises(MetaPublishError, match="no terminó de procesarse"):
        content.publicar_historia("url", "acct", "tok", False, None)

    assert len(sleeps) == content.STATUS_POLL_MAX_ATTEMPTS


def test_publicar_historia_redacta_el_token_en_error_de_polling(monkeypatch):
    token = "SECRET_ACCESS_TOKEN_12345"

    def fake_get(url, params=None, **_kw):
        raise RuntimeError(f"fallo consultando {url}?access_token={token}")

    monkeypatch.setattr(content.requests, "get", fake_get)
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"id": "MEDIA_123"},
    ))

    with pytest.raises(MetaPublishError) as exc_info:
        content.publicar_historia("url", "acct", token, False, None)

    assert token not in str(exc_info.value)


def test_publicar_historia_error_de_polling_incluye_cuerpo_de_meta_y_redacta_token(monkeypatch):
    token = "SECRET_ACCESS_TOKEN_12345"

    class FakeHTTPError(Exception):
        def __init__(self):
            super().__init__(
                f"400 Client Error: Bad Request for url: https://graph.facebook.com/v21.0/1/2?fields=status_code&access_token={token}"
            )
            self.response = SimpleNamespace(
                text='{"error":{"message":"Invalid OAuth access token","type":"OAuthException","code":190}}'
            )

    def fake_get(url, params=None, **_kw):
        raise FakeHTTPError()

    monkeypatch.setattr(content.requests, "get", fake_get)
    monkeypatch.setattr(content.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(content.requests, "post", lambda url, data=None, **_kw: SimpleNamespace(
        ok=True,
        raise_for_status=lambda: None,
        json=lambda: {"id": "MEDIA_123"},
    ))

    with pytest.raises(MetaPublishError) as exc_info:
        content.publicar_historia("url", "acct", token, False, None)

    message = str(exc_info.value)
    assert "respuesta de Meta" in message
    assert "OAuthException" in message
    assert token not in message


def test_prompt_without_tone_examples_does_not_claim_real_examples():
    config = config_cuan(tone_examples=[])
    for prompt in (_armar_prompt(config), content._armar_prompt_historia_unica(config)):
        assert "ejemplos REALES" not in prompt
        assert "Ejemplo 1" not in prompt
        assert "REGLAS DE TONO" in prompt


def test_prompt_with_tone_examples_still_includes_them():
    example = ["uno", "dos", "tres", "cuatro"]
    config = config_cuan(tone_examples=[example])
    for prompt in (_armar_prompt(config), content._armar_prompt_historia_unica(config)):
        assert "ejemplos REALES" in prompt
        assert "Ejemplo 1:" in prompt
        assert "uno ||| dos ||| tres ||| cuatro" in prompt


# ---------------------------------------------------------------------------
# a82: formatos de lanzamiento, estilo de voz y formato/tema por historia
# ---------------------------------------------------------------------------
from app.engine.content import _REGLAS_TONO_POR_DEFECTO, _armar_prompt_historia_unica  # noqa: E402
from app.engine.voces import VOCES  # noqa: E402

_PROHIBIDO = "PROHIBIDO usar vocabulario técnico"


@pytest.mark.parametrize("clave", sorted(FORMATOS))
def test_formato_tiene_maximos_10_8_8_10_y_unica_de_una_oracion(clave):
    formato = FORMATOS[clave]
    maximos = [texto.rsplit("(máx ", 1)[1].split(" ", 1)[0] for texto in formato["arco"]]
    assert maximos == ["10", "8", "8", "10"], clave
    unica = formato["unica"].format(negocio="CUAN")
    assert unica.strip() and "{" not in unica, clave
    assert unica.endswith(".") and unica.count(".") == 1, clave


def test_formatos_de_lanzamiento_existen():
    assert {"cuenta_regresiva", "teaser", "resultado"} <= set(FORMATOS)
    assert len(FORMATOS) == 9


@pytest.mark.parametrize("clave", ["cuenta_regresiva", "resultado"])
def test_formatos_de_lanzamiento_no_inventan_fechas_ni_cifras(clave):
    consignas = " ".join(FORMATOS[clave]["arco"]) + " " + FORMATOS[clave]["unica"]
    assert "solo si figura" in consignas or "si no figura" in consignas


def test_sin_estilo_de_voz_el_tono_es_el_de_siempre():
    config = config_cuan(voice_style=None, weekly_format=None)
    for prompt in (_armar_prompt(config), _armar_prompt_historia_unica(config)):
        assert "REGLAS DE TONO (muy importante):\n" + _REGLAS_TONO_POR_DEFECTO in prompt
        assert _PROHIBIDO in prompt


@pytest.mark.parametrize("clave", sorted(VOCES))
def test_cada_estilo_de_voz_reemplaza_las_vinetas_de_vocabulario(clave):
    config = config_cuan(voice_style=clave)
    reglas = "".join(f"- {regla}\n" for regla in VOCES[clave]["reglas"])
    for prompt in (_armar_prompt(config), _armar_prompt_historia_unica(config)):
        assert "REGLAS DE TONO (muy importante):\n" + reglas in prompt
        assert _PROHIBIDO not in prompt
        assert "Lenguaje simple, cotidiano" not in prompt
    assert "- Cada historia es UNA sola idea, corta, con gancho\n" in _armar_prompt(config)
    assert "- Máximo 12 palabras\n" in _armar_prompt_historia_unica(config)


def test_estilo_de_voz_desconocido_cae_al_tono_de_siempre():
    import random

    config = config_cuan(voice_style="inventado")
    random.seed(7)
    con_clave = _armar_prompt(config)
    random.seed(7)
    assert con_clave == _armar_prompt(config_cuan(voice_style=None))
    assert _armar_prompt_historia_unica(config) == _armar_prompt_historia_unica(config_cuan())


def test_voces_estan_bien_formadas():
    for clave, voz in VOCES.items():
        assert voz["label"].strip() and voz["hint"].strip(), clave
        assert voz["reglas"] and all(r.strip() and not r.startswith("- ") and "\n" not in r for r in voz["reglas"]), clave


def test_historia_unica_con_formato_y_tema():
    config = config_cuan()
    prompt = _armar_prompt_historia_unica(config, formato="teaser", tema="Garmin")

    assert f"Formato de esta historia: Teaser. {FORMATOS['teaser']['unica']}" in prompt
    assert "Tema de esta historia: Garmin. El tema tiene prioridad; la imagen es el contexto visual." in prompt
    assert "que describa o se inspire en lo que se ve en la imagen" not in prompt
    assert "- Máximo 12 palabras\n" in prompt


def test_historia_unica_reemplaza_negocio_en_la_frase_del_formato():
    prompt = _armar_prompt_historia_unica(config_cuan(), formato="comparativa")
    assert "la de CUAN" in prompt and "{negocio}" not in prompt
    assert "Mirá la imagen adjunta con atención" in prompt


def test_historia_unica_ignora_formato_desconocido_y_tema_vacio():
    base = _armar_prompt_historia_unica(config_cuan())
    assert _armar_prompt_historia_unica(config_cuan(), formato="inventado", tema="   ") == base
    assert _armar_prompt_historia_unica(config_cuan(), formato=None, tema=None) == base
    assert "Formato de esta historia" not in base and "Tema de esta historia" not in base


def test_generar_texto_para_imagen_pasa_formato_y_tema_al_prompt(monkeypatch):
    capturado = {}

    def armar(config, formato=None, tema=None):
        capturado.update(formato=formato, tema=tema)
        raise RuntimeError("corte antes de llamar a Claude")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(content, "_armar_prompt_historia_unica", armar)
    with pytest.raises(RuntimeError):
        content.generar_texto_para_imagen(config_cuan(), imagen_falsa(), formato="teaser", tema="Garmin")
    assert capturado == {"formato": "teaser", "tema": "Garmin"}


# ---------------------------------------------------------------------------
# Fase 2: Editar con IA — variantes de una historia
# ---------------------------------------------------------------------------
def _claude_que_responde(monkeypatch, texto, capturado=None):
    class Mensajes:
        def create(self, **kwargs):
            if capturado is not None:
                capturado.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=texto)])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(content.anthropic, "Anthropic", lambda api_key: SimpleNamespace(messages=Mensajes()))


@pytest.mark.parametrize("accion", sorted(content.ACCIONES_VARIANTE))
def test_prompt_de_variantes_por_accion(accion):
    prompt = content._armar_prompt_variantes(config_cuan(), "Tu cocina, a medida.", accion)
    assert content.ACCIONES_VARIANTE[accion] in prompt
    assert '"Tu cocina, a medida."' in prompt
    assert "No inventes precios, fechas, horas, cifras" in prompt
    assert "- Máximo 12 palabras por opción\n" in prompt
    assert _REGLAS_TONO_POR_DEFECTO in prompt


def test_prompt_de_variantes_usa_el_estilo_de_voz_y_el_pedido_libre():
    prompt = content._armar_prompt_variantes(config_cuan(voice_style="sobrio"), "Hola", "libre", "  nombrá el envío gratis ")
    assert "siguiendo este pedido: nombrá el envío gratis" in prompt
    assert _PROHIBIDO not in prompt and "- Tono sobrio y respetuoso, de vos\n" in prompt


@pytest.mark.parametrize("accion, instruccion", [("libre", None), ("libre", "   "), ("inventada", None)])
def test_prompt_de_variantes_rechaza_accion_invalida(accion, instruccion):
    with pytest.raises(ValueError):
        content._armar_prompt_variantes(config_cuan(), "Hola", accion, instruccion)


def test_reescribir_historia_limpia_deduplica_y_descarta_el_original(monkeypatch):
    capturado = {}
    _claude_que_responde(monkeypatch, '1. "Tu cocina, más corta" ||| Tu cocina, a medida. ||| - Otra idea 🔥 ||| tu cocina, más corta ||| Cuarta', capturado)

    variantes = content.reescribir_historia(config_cuan(), "Tu cocina, a medida.", "corto")

    assert variantes == ["Tu cocina, más corta", "Otra idea", "Cuarta"]
    assert capturado["messages"][0]["content"].startswith(config_cuan().business_description)
    assert all(not isinstance(m["content"], list) for m in capturado["messages"])  # solo texto, sin imagen


def test_reescribir_historia_acepta_opciones_por_linea(monkeypatch):
    _claude_que_responde(monkeypatch, "Primera\nSegunda\n")
    assert content.reescribir_historia(config_cuan(), "Original", "otra") == ["Primera", "Segunda"]


def test_reescribir_historia_sin_opciones_nuevas_levanta(monkeypatch):
    _claude_que_responde(monkeypatch, "Original ||| original")
    with pytest.raises(content.ClaudeGenerationError):
        content.reescribir_historia(config_cuan(), "Original", "corto")


# ---------------------------------------------------------------------------
# Fase 4: la IA aprende de las correcciones del equipo
# ---------------------------------------------------------------------------
_APRENDIZAJES = [{"id": "x1", "antes": "Tu cocina soñada te espera.", "despues": "Tu cocina, hecha a medida."},
                 {"id": "x2", "antes": "Consultá   ya", "despues": "Escribí COCINA y te asesoramos"}]


def test_sin_aprendizajes_no_hay_bloque():
    assert content._bloque_aprendizajes(config_cuan()) == ""
    assert "Así corrigió" not in _armar_prompt(config_cuan())


def test_los_aprendizajes_entran_en_los_tres_prompts():
    config = config_cuan(aprendizajes=_APRENDIZAJES)
    bloque = content._bloque_aprendizajes(config)

    assert bloque.startswith("Así corrigió el equipo de CUAN textos que había escrito la IA.")
    assert '- Antes: "Tu cocina soñada te espera." → Después: "Tu cocina, hecha a medida."\n' in bloque
    assert '- Antes: "Consultá ya" → Después: "Escribí COCINA y te asesoramos"\n' in bloque
    for prompt in (_armar_prompt(config), _armar_prompt_historia_unica(config),
                   content._armar_prompt_variantes(config, "Hola", "corto")):
        assert bloque in prompt
        assert prompt.index(bloque) < prompt.index("REGLAS DE TONO")


def test_aprendizajes_limita_a_8_recorta_y_descarta_incompletos():
    largos = [{"antes": f"antes {i} " + "x" * 200, "despues": f"despues {i}"} for i in range(10)]
    config = config_cuan(aprendizajes=[{"antes": "", "despues": "solo despues"}, *largos])
    bloque = content._bloque_aprendizajes(config)
    assert bloque.count("- Antes:") == 8
    assert "solo despues" not in bloque and "antes 8" not in bloque
    assert all(len(line) < 200 for line in bloque.splitlines())


# ---------------------------------------------------------------------------
# Fase 5: onboarding con IA — borrador de la marca
# ---------------------------------------------------------------------------
_PROPUESTA_OK = json.dumps({
    "business_description": "Importadora de tecnología. Hablamos simple y cercano 🚀",
    "tone_examples": [
        ["¿Corrés con el celu?", "Se corta la música.", "Un Garmin mide todo.", "Escribí RUN."],
        ["Solo tres", "historias", "acá"],
        ["Uno", "Dos", "Tres", "Cuatro"], ["A", "B", "C", "D"], ["E", "F", "G", "H"],
    ],
    "topics": ["Garmin para running", "garmin para running", "DJI para creadores", "", "x" * 200],
}, ensure_ascii=False)


def test_proponer_marca_limpia_y_recorta_a_los_limites_del_portal(monkeypatch):
    capturado = {}
    _claude_que_responde(monkeypatch, f"Acá va:\n```json\n{_PROPUESTA_OK}\n```", capturado)

    propuesta = content.proponer_marca("GBA", texto_web="Somos GBA, importamos Garmin y DJI.", notas="Hablamos de vos")

    assert propuesta.business_description == "Importadora de tecnología. Hablamos simple y cercano"
    assert propuesta.tone_examples[0] == ["¿Corrés con el celu?", "Se corta la música.", "Un Garmin mide todo.", "Escribí RUN."]
    assert len(propuesta.tone_examples) == 3 and all(len(hilo) == 4 for hilo in propuesta.tone_examples)
    assert propuesta.topics[:2] == ["Garmin para running", "DJI para creadores"]
    assert all(0 < len(tema) <= 80 for tema in propuesta.topics)
    prompt = capturado["messages"][0]["content"]
    assert "GBA" in prompt and "importamos Garmin" in prompt and "Hablamos de vos" in prompt
    assert isinstance(prompt, str)  # solo texto


def test_proponer_marca_recorta_la_entrada_y_la_descripcion(monkeypatch):
    capturado = {}
    largo = json.dumps({"business_description": "palabra " * 120, "tone_examples": [], "topics": ["Uno"]})
    _claude_que_responde(monkeypatch, largo, capturado)

    propuesta = content.proponer_marca("GBA", texto_web="w" * 50_000)

    assert len(propuesta.business_description) <= 500 and not propuesta.business_description.endswith(" ")
    assert "w" * 8_001 not in capturado["messages"][0]["content"]


def test_proponer_marca_sin_entrada_levanta_value_error():
    with pytest.raises(ValueError):
        content.proponer_marca("GBA", texto_web="  ", notas=None)


@pytest.mark.parametrize("respuesta", ["no es json", json.dumps({"business_description": " ", "topics": []}), "[1, 2]"])
def test_proponer_marca_sin_propuesta_usable_levanta(monkeypatch, respuesta):
    _claude_que_responde(monkeypatch, respuesta)
    with pytest.raises(content.ClaudeGenerationError):
        content.proponer_marca("GBA", notas="Vendemos relojes")


def test_texto_de_respuesta_ignora_bloques_de_thinking():
    response = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="pensando..."),
        SimpleNamespace(type="text", text="Hola"),
        SimpleNamespace(type="text", text=" mundo"),
    ])
    assert content.texto_de_respuesta(response) == "Hola mundo"


def test_las_llamadas_a_claude_no_piden_thinking(mock_servicios):
    content.generar_texto_de_prueba(config_cuan(), imagen_candidata(1))
    _, kwargs = mock_servicios.messages.create.call_args
    assert kwargs["thinking"] == {"type": "disabled"}


def test_prompt_marca_no_deja_prometer_ofertas_ni_inventar_publico():
    prompt = content._armar_prompt_marca("GBA", "Vendemos Garmin.", "")
    assert "combos" in prompt and "servicios" in prompt
    assert "público" in prompt.lower()
