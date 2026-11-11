"""HERRAMIENTAS_CM: el registro versionado de herramientas del CM con IA."""

import json

from app.engine.cm_tools import HERRAMIENTAS_CM
from app.engine.formatos import FORMATOS

LECTURA = {"leer_marca", "leer_semana", "leer_historial", "leer_fotos", "leer_web"}
ESCRITURA = {
    "proponer_descripcion", "proponer_temas", "proponer_ejemplo_tono",
    "proponer_enfoque_semana", "proponer_texto_historia",
}


def _tools():
    return HERRAMIENTAS_CM["v1"]


def _por_nombre():
    return {t["name"]: t for t in _tools()}


def _recorrer(nodo):
    if isinstance(nodo, dict):
        yield nodo
        for v in nodo.values():
            yield from _recorrer(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _recorrer(v)


def test_v1_tiene_exactamente_las_diez_herramientas():
    assert {t["name"] for t in _tools()} == LECTURA | ESCRITURA


def test_ordenadas_por_nombre_para_que_el_prefijo_cacheado_sea_estable():
    nombres = [t["name"] for t in _tools()]
    assert nombres == sorted(nombres)


def test_todas_son_strict_y_cerradas():
    for t in _tools():
        assert t["strict"] is True
        assert t["description"]
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema.get("required", [])) == set(schema["properties"])


def test_todos_los_objetos_anidados_son_cerrados():
    for t in _tools():
        for nodo in _recorrer(t["input_schema"]):
            if nodo.get("type") == "object":
                assert nodo["additionalProperties"] is False


def test_ninguna_herramienta_recibe_ids_de_tenant_ni_url():
    prohibidos = {"client_id", "agency_id", "cliente_id", "agencia_id", "url", "web"}
    for t in _tools():
        claves = {k for nodo in _recorrer(t["input_schema"]) for k in nodo.get("properties", {})}
        assert not (claves & prohibidos), t["name"]


def test_leer_web_no_recibe_argumentos():
    assert _por_nombre()["leer_web"]["input_schema"]["properties"] == {}


def test_leer_fotos_filtra_por_disponibles():
    props = _por_nombre()["leer_fotos"]["input_schema"]["properties"]
    assert set(props) == {"solo_disponibles"}
    assert props["solo_disponibles"]["type"] == "boolean"


def test_formato_del_enfoque_es_las_claves_de_formatos_mas_vacio():
    props = _por_nombre()["proponer_enfoque_semana"]["input_schema"]["properties"]
    assert set(props) == {"enfoque", "formato"}
    assert props["formato"]["enum"] == list(FORMATOS) + [""]


def test_propuestas_exigen_motivo_salvo_enfoque():
    for nombre in ("proponer_descripcion", "proponer_temas", "proponer_ejemplo_tono", "proponer_texto_historia"):
        assert "motivo" in _por_nombre()[nombre]["input_schema"]["properties"]


def test_propuesta_de_texto_apunta_a_una_historia_por_id():
    props = _por_nombre()["proponer_texto_historia"]["input_schema"]["properties"]
    assert set(props) == {"historia_id", "texto", "motivo"}


def test_sin_keywords_no_soportadas_por_strict():
    # strict no admite minLength/maxLength/minItems>1/maxItems/minimum/maximum/pattern
    prohibidas = {"minLength", "maxLength", "maxItems", "minimum", "maximum", "pattern", "oneOf", "format"}
    for t in _tools():
        for nodo in _recorrer(t["input_schema"]):
            assert not (set(nodo) & prohibidas), t["name"]
            assert nodo.get("minItems", 0) in (0, 1)


def test_serializable():
    json.dumps(_tools())


def test_descripcion_se_escribe_en_segunda_persona_como_instruccion_para_quien_escribe():
    tool = _por_nombre()["proponer_descripcion"]
    textos = tool["description"] + " " + tool["input_schema"]["properties"]["descripcion"]["description"]
    assert "segunda persona" in textos
    assert "Sos " in textos
    assert "le hablás" in textos
    assert "hablás" in textos
    assert "oración completa" in textos
    assert "500" in textos
    assert set(tool["input_schema"]["properties"]) == {"descripcion", "motivo"}

