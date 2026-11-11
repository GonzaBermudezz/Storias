"""Etiquetas de las fotos de Drive: ¿hay una persona? ¿de qué tipo de escena es?

Se pregunta a Claude (visión) una sola vez por foto y el resultado se guarda en
``image_tags`` (ver content_jobs). Todo es "mejor esfuerzo": si algo falla se
devuelve None y la foto queda sin etiquetar; NUNCA se rompe una generación por esto.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic
from PIL import Image

from app.engine.content import CLAUDE_MODEL, CLAUDE_THINKING, texto_de_respuesta

logger = logging.getLogger(__name__)

SCENES = ("producto", "ambiente", "detalle", "exterior", "grafico", "otro")
_MAX_SIDE = 768
_MAX_WORKERS = 4
_MAX_PRODUCTO = 60
_PROMPT = (
    "Mirá la foto y devolvé SOLO un JSON con tres campos, sin texto extra:\n"
    '{"persona": true o false, "escena": "<una de: producto, ambiente, detalle, exterior, grafico, otro>", '
    '"producto": "<texto corto>"}\n\n'
    "persona = true SOLO si hay una persona claramente visible (su cara o su cuerpo es protagonista). "
    "Manos solas, siluetas lejanas o personas muy chicas de fondo = false.\n"
    "escena: producto = objeto o trabajo terminado en primer plano; ambiente = vista general de un "
    "espacio interior; detalle = primer plano de un material, textura o pieza; exterior = fachada, "
    "calle o paisaje; grafico = texto, logo, diseño o captura de pantalla; otro = nada de lo anterior.\n"
    "producto = el nombre corto del objeto o producto principal que se ve, con la marca y el modelo "
    "SOLO si se leen en la foto o son inconfundibles (ej.: \"reloj Garmin Forerunner\", \"drone DJI\", "
    "\"camiseta\"). Si no hay un producto claro, dejalo vacío (\"\")."
)


def _preparar(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


def parse_tag(text: str | None) -> dict | None:
    """Convierte la respuesta de Claude en {"persona": bool, "escena": str, "producto": str}
    o None. ``producto`` es opcional en la respuesta: si falta o no es texto, queda ""."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("persona"), bool):
        return None
    escena = str(data.get("escena", "")).strip().lower()
    producto = data.get("producto")
    producto = producto.strip()[:_MAX_PRODUCTO] if isinstance(producto, str) else ""
    return {"persona": data["persona"], "escena": escena if escena in SCENES else "otro",
            "producto": producto}


def classify_image(image_bytes: bytes) -> dict | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=1)
        response = client.messages.create(
            model=os.getenv("IMAGE_TAG_MODEL") or CLAUDE_MODEL,
            max_tokens=100,
            thinking=CLAUDE_THINKING,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": _preparar(image_bytes)}},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        return parse_tag(texto_de_respuesta(response))
    except Exception:
        logger.warning("No se pudo clasificar una foto de Drive", exc_info=True)
        return None


def classify_many(rows: list[tuple[str, str, bytes]]) -> dict[str, dict]:
    """rows = [(drive_file_id, nombre, bytes)]. Devuelve {id: etiqueta} solo de las que se pudieron clasificar."""
    if not rows:
        return {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        tags = list(pool.map(lambda row: classify_image(row[2]), rows))
    return {row[0]: tag for row, tag in zip(rows, tags) if tag}
