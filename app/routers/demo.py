"""Sirve los prototipos HTML del scratchpad. Solo en development."""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from app.config import get_settings

router = APIRouter(tags=["demo"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent.parent / "templates"))

# Directorio base donde viven los HTML de prototipo
# En dev buscamos en scratchpad o en la carpeta del proyecto
_BASE_DIRS = [
    Path(__file__).parent.parent.parent / "scratchpad",  # storias/scratchpad/
    Path(
        r"C:\Users\avaca\AppData\Local\Temp\claude"
        r"\C--Users-avaca-OneDrive-Escritorio-web-BotPublicidad-testFixed"
        r"\ccfa3ee6-7a96-4dc0-8a09-74d111836b60\scratchpad"
    ),
]

DEMOS = {
    "opcion1": "opcion1-cliente.html",
    "opcion2": "opcion2-empleados.html",
    "opcion3": "opcion3-whitelabel.html",
    "aprobar": "aprobar.html",
}


@router.get("/demo/{opcion}")
async def demo(opcion: str):
    """Sirve el prototipo HTML. Sin auth en dev para facilitar el testing."""
    s = get_settings()
    if s.is_production:
        raise HTTPException(status_code=404)  # no existe en prod

    filename = DEMOS.get(opcion)
    if not filename:
        raise HTTPException(status_code=404, detail="Demo no encontrada")

    for base in _BASE_DIRS:
        p = base / filename
        if p.exists():
            return FileResponse(str(p), media_type="text/html")

    raise HTTPException(status_code=404, detail=f"Archivo {filename} no encontrado en ningún directorio")
