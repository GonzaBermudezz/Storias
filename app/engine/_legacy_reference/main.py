"""
Instagram Stories Automation - CUAN Arquitectura
=================================================
Flujo dos etapas:
  Viernes 18:00  -> generar_hilos_semana()      : Drive -> Claude -> Pillow -> Cloudinary -> Drive (JSON)
  Dias programados -> publicar_hilos_pendientes() : Drive (JSON) -> Meta API
"""

import os
import io
import re
import json
import random
import base64
import requests
import schedule
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    print("[AVISO] pillow-heif no instalado: archivos HEIC no seran soportados.")

import anthropic
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------
# CONFIGURACION
# -----------------------------------------
CONFIG = {
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),

    "GOOGLE_DRIVE_FOLDER_ID": "1rYVntsBA9Re9OWGIGjB6onh3bRClvggM",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "service_account.json",

    "CLOUDINARY_CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
    "CLOUDINARY_API_KEY": os.getenv("CLOUDINARY_API_KEY"),
    "CLOUDINARY_API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),

    "INSTAGRAM_BUSINESS_ACCOUNT_ID": os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", ""),
    "META_ACCESS_TOKEN": os.getenv("META_ACCESS_TOKEN", ""),

    "CALENDLY_LINK": "",
    "PROB_LINK": 0.0,

    "SCHEDULE_DAYS": ["monday", "wednesday", "friday"],
    "SCHEDULE_TIME": "09:00",
    "GENERATION_TIME": "18:00",   # viernes, fijo en codigo

    "ESTUDIO_NOMBRE": "C U A N",
    "OUTPUT_DIR": "output",

    # Archivos JSON en Drive
    "ESTADO_FILENAME":  "cuan_estado.json",
    "HISTORIAL_FILENAME": "cuan_historial.json",
    "LOG_FILENAME":     "cuan_log.json",
    "HILOS_FILENAME":   "cuan_hilos_pendientes.json",

    # Email
    "EMAIL_TO": os.getenv("EMAIL_TO", ""),
}

# -----------------------------------------
# EMAIL
# -----------------------------------------
import resend as _resend
_resend.api_key = os.getenv("RESEND_API_KEY", "")

def enviar_mail(asunto, cuerpo, destino=None):
    """
    Envía un email via Resend API.
    Si RESEND_API_KEY o el destinatario no están configurados, loguea y retorna sin error.
    destino permite sobreescribir EMAIL_TO puntualmente.
    """
    if not _resend.api_key:
        print(f"[Mail] Sin API key — asunto omitido: {asunto}")
        return

    to_addr = destino or CONFIG["EMAIL_TO"]
    if not to_addr:
        print(f"[Mail] Sin destinatario — asunto omitido: {asunto}")
        return

    try:
        _resend.Emails.send({
            "from": "CUAN Bot <onboarding@resend.dev>",
            "to": to_addr,
            "subject": asunto,
            "text": cuerpo,
        })
        print(f"[Mail] Enviado: {asunto}")
    except Exception as e:
        print(f"[Mail] Error al enviar ({asunto}): {e}")


# -----------------------------------------
# GOOGLE DRIVE — helpers de modulo
# NOTA: cuan_hilos_pendientes.json debe existir en Drive antes de la primera ejecucion.
# La service account no puede crear archivos nuevos en Drive personal; solo modificar existentes.
# -----------------------------------------
def _drive_svc():
    """Devuelve un servicio de Drive con permisos completos."""
    creds = service_account.Credentials.from_service_account_file(
        CONFIG["GOOGLE_SERVICE_ACCOUNT_JSON"],
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def _drive_leer_json(svc, filename, default=None):
    FOLDER_ID = CONFIG["GOOGLE_DRIVE_FOLDER_ID"]
    try:
        r = svc.files().list(
            q=f"name='{filename}' and '{FOLDER_ID}' in parents and trashed=false",
            fields="files(id)",
        ).execute()
        files = r.get("files", [])
        if not files:
            return default
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=files[0]["id"]))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return json.loads(buf.getvalue().decode("utf-8"))
    except Exception as e:
        print(f"[Drive] Error leyendo {filename}: {e}")
        return default


def _drive_escribir_json(svc, filename, data):
    FOLDER_ID = CONFIG["GOOGLE_DRIVE_FOLDER_ID"]
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json")
    r = svc.files().list(
        q=f"name='{filename}' and '{FOLDER_ID}' in parents and trashed=false",
        fields="files(id)",
    ).execute()
    files = r.get("files", [])
    if files:
        svc.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        print(f"[Drive] Advertencia: {filename} no existe. Creando de todas formas...")
        svc.files().create(
            body={"name": filename, "parents": [FOLDER_ID]},
            media_body=media,
        ).execute()


def _drive_agregar_log(svc, mensaje, tipo="info"):
    log = _drive_leer_json(svc, CONFIG["LOG_FILENAME"], default=[])
    log.insert(0, {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mensaje": mensaje,
        "tipo": tipo,
    })
    _drive_escribir_json(svc, CONFIG["LOG_FILENAME"], log[:100])


# -----------------------------------------
# IMAGENES
# -----------------------------------------
def abrir_imagen(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGBA")


def _descargar_archivo_drive(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def preparar_imagen_para_claude(image_bytes: bytes) -> tuple[bytes, str]:
    """Convierte cualquier imagen soportada a JPEG para Claude Vision."""
    img = abrir_imagen(image_bytes)
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue(), "image/jpeg"


# -----------------------------------------
# GOOGLE DRIVE — bajar foto aleatoria
# -----------------------------------------
def get_random_image_from_drive():
    """Legacy: una imagen al azar. Usa list_valid_images_from_drive internamente."""
    pool = list_valid_images_from_drive()
    if not pool:
        raise ValueError("No hay imagenes validas en Drive.")
    return pool[0]


def list_valid_images_from_drive():
    """
    Devuelve una lista (shuffled) de tuplas (image_bytes, name) con todas las imagenes
    validas de Drive. Las descarga y verifica una sola vez.
    """
    creds = service_account.Credentials.from_service_account_file(
        CONFIG["GOOGLE_SERVICE_ACCOUNT_JSON"],
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    service = build("drive", "v3", credentials=creds)

    results = service.files().list(
        q=f"'{CONFIG['GOOGLE_DRIVE_FOLDER_ID']}' in parents and mimeType contains 'image/' and trashed=false",
        fields="files(id, name)",
        pageSize=100,
    ).execute()

    files = results.get("files", [])
    if not files:
        raise ValueError("No hay imagenes en la carpeta de Drive.")

    random.shuffle(files)
    validas = []
    errores = []

    for f in files:
        try:
            image_bytes = _descargar_archivo_drive(service, f["id"])
            img = abrir_imagen(image_bytes)
            img.load()
            img.close()
            validas.append((image_bytes, f["name"]))
            if len(validas) >= 20:
                break
        except Exception as e:
            errores.append(f"{f['name']}: {e}")
            print(f"[Drive] Saltando {f['name']} (no se pudo abrir)")

    if not validas:
        raise ValueError(
            "No se pudo abrir ninguna imagen de Drive. "
            f"Probadas: {len(files)}. Ultimos errores: {errores[:3]}"
        )

    random.shuffle(validas)
    print(f"[Drive] {len(validas)} imagenes validas disponibles")
    return validas


# -----------------------------------------
# CLAUDE — generar hilo de 4 historias basado en la imagen
# -----------------------------------------
def generar_hilo(image_bytes: bytes) -> list:
    """Genera un hilo de 4 historias analizando la imagen con Claude Vision."""
    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])

    image_bytes, media_type = preparar_imagen_para_claude(image_bytes)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    temas_posibles = [
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
    tema_elegido = random.choice(temas_posibles)

    prompt = (
        "Sos el copywriter de CUAN, un estudio que diseña cocinas "
        "a medida para personas que están construyendo o remodelando "
        "su casa. Tu público NO son arquitectos ni diseñadores: son "
        "personas comunes armando su casa, muchas veces por primera vez.\n\n"
        "Generá UN hilo de exactamente 4 historias de Instagram sobre "
        f"este tema: {tema_elegido}\n\n"
        "Estos son ejemplos REALES de hilos que ya publicó CUAN y que "
        "funcionaron muy bien. Imitá el tono, la simpleza y la "
        "estructura exacta de estos ejemplos (no los copies, son "
        "solo referencia de estilo):\n\n"
        "Ejemplo 1:\n"
        "El error más caro de una cocina no es la mesada. ||| Es construir algo que después no funciona. ||| Por eso diseñamos todo en 3D antes de la obra. ||| ¿Pensás renovar tu cocina? Escribí COCINA.\n\n"
        "Ejemplo 2:\n"
        "¿Tu cocina se siente chica? ||| Muchas veces no faltan metros. ||| Falta una mejor distribución. ||| Mandá DISEÑO y te mostramos posibilidades.\n\n"
        "Ejemplo 3:\n"
        "La mayoría elige materiales demasiado pronto. ||| Y se olvida de lo más importante. ||| La distribución. ||| Escribí PLAN y te contamos cómo trabajamos.\n\n"
        "Ejemplo 4:\n"
        "¿Querés una isla? ||| No siempre es la mejor solución. ||| Cada cocina necesita una estrategia distinta. ||| Mandá PROYECTO y lo vemos juntos.\n\n"
        "Ejemplo 5:\n"
        "Tu cocina te va a acompañar años. ||| No diseñes a prueba y error. ||| Visualizala completa antes de construir. ||| Escribí COCINA para agendar una reunión.\n\n"
        "Ejemplo 6:\n"
        "Una decisión puede arruinar toda una cocina. ||| Y la mayoría la toma demasiado rápido. ||| Te contamos cuál es en la reunión. ||| Mandá QUIERO y coordinamos.\n\n"
        "REGLAS DE TONO (muy importante):\n"
        "- Lenguaje simple, cotidiano, como le hablarías a un amigo "
        "que está construyendo su casa\n"
        "- PROHIBIDO usar vocabulario técnico de arquitectura o diseño "
        "(nada de \"campana\", \"cantos\", \"isla funcional\", \"zócalo\", "
        "\"revestimiento\", etc.) salvo que sea una palabra que "
        "cualquier persona común entendería sin pensar\n"
        "- Cada historia es UNA sola idea, corta, con gancho\n"
        "- Historia 1: una afirmación o pregunta que genera intriga, "
        "conectada al tema (máx 10 palabras)\n"
        "- Historia 2: desarrolla la tensión o el problema (máx 8 palabras)\n"
        "- Historia 3: el diferencial de CUAN, la solución (máx 8 palabras)\n"
        "- Historia 4: CTA con una palabra clave en mayúsculas para "
        "escribir y agendar (máx 10 palabras)\n\n"
        "La imagen adjunta es solo inspiración visual de fondo, NO "
        "es necesario describir literalmente lo que se ve en ella. "
        "El tema asignado y el tono de los ejemplos tienen prioridad "
        "total sobre cualquier detalle de la imagen.\n\n"
        "Sin hashtags. Sin emojis, excepto opcionalmente 1 en la "
        "historia 4 si suma.\n\n"
        "Devolvé SOLO las 4 historias separadas por |||, sin "
        "numeración ni etiquetas."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    def _limpiar_emojis(texto: str) -> str:
        """Elimina emojis y símbolos fuera del rango Latino, conservando español."""
        return re.sub(r'[^\x20-\x7E\u00A0-\u024F]', '', texto).strip()

    raw = response.content[0].text.strip()
    historias = [_limpiar_emojis(h.strip()) for h in raw.split("|||")]
    while len(historias) < 4:
        historias.append("Escribi COCINA y coordinamos una reunion.")
    historias = historias[:4]

    print(f"[Claude] Hilo generado:")
    for i, h in enumerate(historias, 1):
        print(f"  Historia {i}: {h}")
    return historias


# -----------------------------------------
# PILLOW — editar imagen estilo CUAN
# -----------------------------------------
def editar_imagen(image_bytes, frase, agregar_cta, num_historia=1):
    img = abrir_imagen(image_bytes)
    target_w, target_h = 1080, 1920
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_h = img.height
        new_w = int(new_h * target_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, new_h))
    else:
        new_w = img.width
        new_h = int(new_w / target_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, new_w, top + new_h))

    img = img.resize((target_w, target_h), Image.LANCZOS)

    img_rgb = img.convert("RGB")
    from PIL import ImageEnhance
    img_rgb = ImageEnhance.Contrast(img_rgb).enhance(0.82)
    img_rgb = ImageEnhance.Color(img_rgb).enhance(0.88)

    r, g, b = img_rgb.split()
    from PIL import Image as PILImage
    r = r.point(lambda i: min(255, int(i * 1.04)))
    b = b.point(lambda i: int(i * 0.94))
    img_rgb = PILImage.merge("RGB", (r, g, b))

    img_rgba = img_rgb.convert("RGBA")
    overlay = PILImage.new("RGBA", img_rgba.size, (0, 0, 0, 65))
    img_final = PILImage.alpha_composite(img_rgba, overlay)
    draw = ImageDraw.Draw(img_final)

    FONT_WEIGHT = 600  # SemiBold — legible sobre fotos sin perder elegancia

    def load_font(paths, size, weight=FONT_WEIGHT):
        for path in paths:
            try:
                f = ImageFont.truetype(path, size)
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass  # fuente estática, sin eje de peso
                return f
            except Exception:
                continue
        return ImageFont.load_default()

    MONTSERRAT = [
        str(Path(__file__).parent / "Montserrat[wght].ttf"),
        str(Path(__file__).parent / "Raleway[wght].ttf"),
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/calibril.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    font_size = 75
    font_texto = load_font(MONTSERRAT, font_size)

    WHITE     = (255, 255, 255, 255)
    WHITE_DIM = (255, 255, 255, 190)

    margin = 85
    max_w = target_w - margin * 2

    def wrap_text(text, font, max_w):
        words = text.split()
        lines, line = [], ""
        for w in words:
            test = f"{line} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] > max_w and line:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)
        return lines

    lines = wrap_text(frase, font_texto, max_w)
    line_h = font_size + 35
    total_text_h = len(lines) * line_h

    pos_ratios = {1: 0.42, 2: 0.50, 3: 0.54, 4: 0.46}
    base_y = int(target_h * pos_ratios.get(num_historia, 0.46)) - total_text_h // 2

    alineacion = "centro" if num_historia == 1 else "izquierda"

    for i, line in enumerate(lines):
        y = base_y + i * line_h
        if alineacion == "centro":
            bbox = draw.textbbox((0, 0), line, font=font_texto)
            x = (target_w - (bbox[2] - bbox[0])) // 2
        else:
            x = margin
        draw.text((x, y), line, font=font_texto, fill=WHITE)

    if agregar_cta and CONFIG["CALENDLY_LINK"]:
        font_cta = load_font(MONTSERRAT, 50)
        cta_y = base_y + total_text_h + 60
        draw.text((margin, cta_y), "Agenda tu consulta", font=font_cta, fill=WHITE_DIM)

    logo_path = Path(__file__).parent / "cuan_logo.png"
    if logo_path.exists():
        logo = PILImage.open(logo_path).convert("RGBA")
        alpha = logo.split()[3]
        logo = PILImage.new("RGBA", logo.size, (255, 255, 255, 180))
        logo.putalpha(alpha.point(lambda a: 180 if a > 10 else 0))
        logo_target_w = 240
        ratio = logo_target_w / logo.width
        logo_h = int(logo.height * ratio)
        logo = logo.resize((logo_target_w, logo_h), PILImage.LANCZOS)
        logo_x = (target_w - logo_target_w) // 2
        logo_y = target_h - 220
        img_final.paste(logo, (logo_x, logo_y), logo)
        draw = ImageDraw.Draw(img_final)

    output = io.BytesIO()
    img_final.convert("RGB").save(output, format="JPEG", quality=95)
    return output.getvalue()


# -----------------------------------------
# CLOUDINARY — subir imagen
# -----------------------------------------
def subir_a_cloudinary(image_bytes, nombre):
    cloudinary.config(
        cloud_name=CONFIG["CLOUDINARY_CLOUD_NAME"],
        api_key=CONFIG["CLOUDINARY_API_KEY"],
        api_secret=CONFIG["CLOUDINARY_API_SECRET"],
    )
    public_id = f"cuan_ig/{nombre}_{int(time.time())}"
    result = cloudinary.uploader.upload(
        image_bytes,
        public_id=public_id,
        resource_type="image",
    )
    url = result["secure_url"]
    print(f"[Cloudinary] URL publica: {url}")
    return url


# -----------------------------------------
# META API — publicar historia
# -----------------------------------------
def publicar_historia(image_url, agregar_cta):
    account_id = CONFIG["INSTAGRAM_BUSINESS_ACCOUNT_ID"]
    token = CONFIG["META_ACCESS_TOKEN"]

    if not account_id or not token:
        print("[Meta] Credenciales no configuradas - saltando publicacion.")
        return False

    base = f"https://graph.facebook.com/v21.0/{account_id}"
    payload = {
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": token,
    }

    if agregar_cta and CONFIG["CALENDLY_LINK"]:
        payload["story_cta"] = json.dumps([{
            "type": "SWIPE_UP",
            "param": {"link": CONFIG["CALENDLY_LINK"]},
        }])

    r = requests.post(f"{base}/media", data=payload)
    if not r.ok:
        print(f"[Meta] Error al crear media container: {r.status_code} — {r.text}")
        r.raise_for_status()
    media_id = r.json()["id"]
    print(f"[Meta] Media container creado: {media_id}")

    r2 = requests.post(f"{base}/media_publish", data={
        "creation_id": media_id,
        "access_token": token,
    })
    if not r2.ok:
        print(f"[Meta] Error al publicar: {r2.status_code} — {r2.text}")
        r2.raise_for_status()
    post_id = r2.json()["id"]
    print(f"[Meta] Historia publicada ID: {post_id}")
    return True


# -----------------------------------------
# HISTORIAL — guardar en Drive para el panel
# -----------------------------------------
def guardar_en_historial(svc, url_imagen, frase, con_cta, publicado=False):
    """Actualiza historial, log y ultima_ejecucion en Drive."""
    try:
        historial = _drive_leer_json(svc, CONFIG["HISTORIAL_FILENAME"], default=[])
        historial.insert(0, {
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "frase": frase,
            "imagen_url": url_imagen,
            "con_cta": con_cta,
            "estado": "publicado" if publicado else "generado",
        })
        _drive_escribir_json(svc, CONFIG["HISTORIAL_FILENAME"], historial[:50])

        estado_txt = "publicado en Instagram" if publicado else "generado correctamente"
        _drive_agregar_log(svc, f"Hilo {estado_txt}", "success" if publicado else "info")

        estado = _drive_leer_json(svc, CONFIG["ESTADO_FILENAME"], {
            "dias": CONFIG["SCHEDULE_DAYS"],
            "hora": CONFIG["SCHEDULE_TIME"],
            "activo": True,
            "ultima_ejecucion": None,
        })
        estado["ultima_ejecucion"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        _drive_escribir_json(svc, CONFIG["ESTADO_FILENAME"], estado)

        print("[Panel] Historial actualizado en Drive")
    except Exception as e:
        print(f"[Panel] Warning: {e}")


# -----------------------------------------
# ETAPA 1 — GENERACION (viernes 18:00)
# -----------------------------------------
def generar_hilos_semana():
    """
    Genera los hilos para los dias programados de la semana siguiente
    y los guarda en Drive como cuan_hilos_pendientes.json.
    NO publica en Instagram.
    """
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Generando hilos — semana siguiente")
    print(f"{'='*50}")

    svc = _drive_svc()

    estado = _drive_leer_json(svc, CONFIG["ESTADO_FILENAME"], {
        "dias": CONFIG["SCHEDULE_DAYS"],
        "hora": CONFIG["SCHEDULE_TIME"],
        "activo": True,
    })

    if not estado.get("activo", True):
        print("[Gen] Bot pausado. Omitiendo generacion.")
        return

    dias_programados = estado.get("dias", CONFIG["SCHEDULE_DAYS"])

    # Calcular fechas de la semana siguiente
    hoy = datetime.now().date()
    day_num = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    # Lunes de la semana siguiente (nunca es el lunes actual)
    dias_hasta_lunes = (7 - hoy.weekday()) % 7 or 7
    proximo_lunes = hoy + timedelta(days=dias_hasta_lunes)

    # Leer hilos existentes para no duplicar
    hilos_pendientes = _drive_leer_json(svc, CONFIG["HILOS_FILENAME"], default=[])

    nuevos = []
    errores = []

    # Pool unico de imagenes para toda la semana (evita repetir entre hilos)
    pool_semanal = list_valid_images_from_drive()
    if not pool_semanal:
        raise ValueError("Sin imagenes disponibles en Drive.")

    imagenes_por_hilo = 4
    total_necesarias = len(dias_programados) * imagenes_por_hilo
    if len(pool_semanal) < total_necesarias:
        print(f"[Gen] AVISO: {len(pool_semanal)} imagenes disponibles, "
              f"necesitas {total_necesarias} para {len(dias_programados)} dias")

    for dia in dias_programados:
        fecha = proximo_lunes + timedelta(days=day_num[dia])
        hilo_id = f"hilo_{fecha.strftime('%Y%m%d')}"

        if any(h["id"] == hilo_id for h in hilos_pendientes):
            print(f"[Gen] {hilo_id} ya existe, saltando.")
            continue

        print(f"\n[Gen] Generando hilo para {dia} {fecha}...")
        try:
            if len(pool_semanal) < imagenes_por_hilo:
                raise ValueError(f"No quedan suficientes imagenes unicas para {hilo_id} "
                                 f"(quedan {len(pool_semanal)}, necesitas {imagenes_por_hilo})")

            image_bytes_principal, nombre_principal = pool_semanal.pop(0)
            historias = generar_hilo(image_bytes_principal)

            urls = []
            urls_raw = []
            for i, texto in enumerate(historias, 1):
                if i == 1:
                    img_b = image_bytes_principal
                else:
                    img_b, _ = pool_semanal.pop(0)

                # Guardar imagen sin texto para permitir re-edición posterior
                url_raw = subir_a_cloudinary(img_b, f"{dia}_{i}_{fecha.strftime('%Y%m%d')}_raw")
                urls_raw.append(url_raw)

                agregar_cta = (i == 4) and CONFIG["PROB_LINK"] > 0
                imagen_editada = editar_imagen(img_b, texto, agregar_cta, num_historia=i)
                url = subir_a_cloudinary(
                    imagen_editada,
                    f"{dia}_{i}_{fecha.strftime('%Y%m%d')}",
                )
                urls.append(url)

            hilo = {
                "id": hilo_id,
                "fecha_publicacion": fecha.strftime("%Y-%m-%d"),
                "dia": dia,
                "historias": historias,
                "imagenes_url": urls,
                "imagenes_originales_url": urls_raw,
                "estado": "pendiente",
                "editado": False,
            }
            hilos_pendientes.append(hilo)
            nuevos.append(hilo_id)
            print(f"[Gen] {hilo_id} generado OK.")

        except Exception as e:
            print(f"[Gen] Error generando {hilo_id}: {e}")
            errores.append(f"{dia}: {e}")

    # Guardar en Drive aunque no haya nuevos (limpia posibles duplicados)
    _drive_escribir_json(svc, CONFIG["HILOS_FILENAME"], hilos_pendientes)

    semana_str = proximo_lunes.strftime("%d/%m")
    if nuevos:
        msg = f"Generados {len(nuevos)} hilos para semana del {semana_str}"
        if errores:
            msg += f" ({len(errores)} con error)"
        _drive_agregar_log(svc, msg, "success" if not errores else "error")
        estado["ultima_generacion"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        _drive_escribir_json(svc, CONFIG["ESTADO_FILENAME"], estado)

        # Armar cuerpo del mail con detalle de cada hilo generado
        lineas = [
            f"Semana del {semana_str}",
            f"Generados: {len(nuevos)}  |  Errores: {len(errores)}",
            "",
        ]
        for hilo in hilos_pendientes:
            if hilo["id"] in nuevos:
                lineas.append(f"— {hilo['fecha_publicacion']} ({hilo['dia']})")
                for i, texto in enumerate(hilo["historias"], 1):
                    lineas.append(f"  Historia {i}: {texto}")
                lineas.append("")
        if errores:
            lineas.append("Errores:")
            lineas.extend(f"  {e}" for e in errores)
        enviar_mail(
            "CUAN — Hilos generados para la semana",
            "\n".join(lineas),
        )
    elif errores:
        _drive_agregar_log(svc, f"Error en generacion semana {semana_str}", "error")

    print(f"\n[Gen] Completado: {len(nuevos)} nuevos, {len(errores)} errores.")


# -----------------------------------------
# ETAPA 2 — PUBLICACION (dias programados)
# -----------------------------------------
def publicar_hilos_pendientes():
    """
    Publica en Instagram los hilos pendientes para HOY.
    Lee cuan_hilos_pendientes.json, filtra por fecha y estado='pendiente',
    publica y actualiza el estado a 'publicado'.
    """
    hoy = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Publicando hilos del dia — CUAN Arquitectura")
    print(f"{'='*50}")

    svc = _drive_svc()
    hilos = _drive_leer_json(svc, CONFIG["HILOS_FILENAME"], default=[])

    hilos_hoy = [
        h for h in hilos
        if h.get("fecha_publicacion") == hoy and h.get("estado") == "pendiente"
    ]

    if not hilos_hoy:
        print(f"[Pub] No hay hilos pendientes para hoy ({hoy}).")
        _drive_agregar_log(svc, f"Sin hilos pendientes para hoy ({hoy})", "info")
        return

    print(f"[Pub] {len(hilos_hoy)} hilo(s) para publicar hoy.")

    for hilo in hilos_hoy:
        print(f"\n[Pub] Publicando {hilo['id']}...")
        try:
            for i, (texto, url) in enumerate(zip(hilo["historias"], hilo["imagenes_url"]), 1):
                print(f"  Historia {i}: {texto[:50]}...")
                agregar_cta = (i == 4) and CONFIG["PROB_LINK"] > 0
                publicar_historia(url, agregar_cta)
                if i < 4:
                    time.sleep(3)

            hilo["estado"] = "publicado"
            guardar_en_historial(
                svc,
                hilo["imagenes_url"][0],
                " / ".join(hilo["historias"]),
                False,
                publicado=True,
            )
            print(f"[Pub] {hilo['id']} publicado exitosamente.")

        except Exception as e:
            hilo["estado"] = "error"
            print(f"[Pub] Error publicando {hilo['id']}: {e}")
            _drive_agregar_log(svc, f"Error publicando {hilo['id']}: {e}", "error")

    # Guardar estados actualizados
    _drive_escribir_json(svc, CONFIG["HILOS_FILENAME"], hilos)

    publicados = sum(1 for h in hilos_hoy if h.get("estado") == "publicado")
    print(f"\n[Pub] {publicados}/{len(hilos_hoy)} hilos publicados.")

    if publicados:
        hora_actual = datetime.now().strftime("%H:%M")
        lineas = [
            f"Fecha: {hoy}  |  Hora: {hora_actual}",
            f"Publicados: {publicados}/{len(hilos_hoy)}",
            "",
        ]
        for hilo in hilos_hoy:
            estado_hilo = hilo.get("estado", "?")
            lineas.append(f"— {hilo['id']} [{estado_hilo}]")
            if estado_hilo == "publicado":
                for i, texto in enumerate(hilo["historias"], 1):
                    lineas.append(f"  Historia {i}: {texto}")
            lineas.append("")
        enviar_mail(
            "CUAN — Historias publicadas en Instagram",
            "\n".join(lineas),
        )


# -----------------------------------------
# MODO LEGACY — genera Y publica en el momento (--ahora / testing)
# -----------------------------------------
def publicar_historia_automatica():
    """Flujo original: genera y publica de inmediato. Solo para testing manual."""
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Modo legacy — CUAN Arquitectura")
    print(f"{'='*50}")

    try:
        image_bytes_principal, nombre_principal = get_random_image_from_drive()
        historias = generar_hilo(image_bytes_principal)

        out_dir = Path(CONFIG["OUTPUT_DIR"])
        out_dir.mkdir(exist_ok=True)
        urls = []

        for i, texto in enumerate(historias, 1):
            print(f"\n[Hilo] Historia {i}/4: {texto[:50]}...")
            if i == 1:
                image_bytes, nombre = image_bytes_principal, nombre_principal
            else:
                image_bytes, nombre = get_random_image_from_drive()

            agregar_cta = (i == 4) and CONFIG["PROB_LINK"] > 0
            imagen_editada = editar_imagen(image_bytes, texto, agregar_cta, num_historia=i)

            out_path = out_dir / f"historia_{i}_{int(time.time())}.jpg"
            out_path.write_bytes(imagen_editada)
            print(f"[Local] Guardada: {out_path.name}")

            url = subir_a_cloudinary(imagen_editada, f"hilo_{i}_{Path(nombre).stem}")
            urls.append(url)
            publicar_historia(url, agregar_cta)

            if i < 4:
                time.sleep(3)

        svc = _drive_svc()
        publicado_en_ig = bool(CONFIG["INSTAGRAM_BUSINESS_ACCOUNT_ID"] and CONFIG["META_ACCESS_TOKEN"])
        guardar_en_historial(svc, urls[0], " / ".join(historias), False, publicado=publicado_en_ig)
        estado_txt = "publicado en Instagram" if publicado_en_ig else "generado (sin Meta configurado)"
        print(f"\nHilo completo {estado_txt} ({datetime.now().strftime('%H:%M')})")

    except Exception as e:
        print(f"\nError: {e}")
        raise


# -----------------------------------------
# SCHEDULER
# -----------------------------------------
def iniciar_scheduler():
    print("\nLeyendo configuracion desde Drive...")

    try:
        svc = _drive_svc()
        estado = _drive_leer_json(svc, CONFIG["ESTADO_FILENAME"], None)
        if estado:
            hora          = estado.get("hora", CONFIG["SCHEDULE_TIME"])
            dias          = estado.get("dias", CONFIG["SCHEDULE_DAYS"])
            dias_con_hora = estado.get("dias_con_hora", {})
            activo        = estado.get("activo", True)
            print(f"[Drive] Config: {dias} — activo: {activo}")
        else:
            hora, dias, dias_con_hora, activo = CONFIG["SCHEDULE_TIME"], CONFIG["SCHEDULE_DAYS"], {}, True
            print("[Drive] Usando config por defecto")
    except Exception as e:
        hora, dias, dias_con_hora, activo = CONFIG["SCHEDULE_TIME"], CONFIG["SCHEDULE_DAYS"], {}, True
        print(f"[Drive] Error leyendo config: {e}")

    if not activo:
        print("Bot pausado desde el panel.")
        return

    day_map = {
        "monday":    schedule.every().monday,
        "tuesday":   schedule.every().tuesday,
        "wednesday": schedule.every().wednesday,
        "thursday":  schedule.every().thursday,
        "friday":    schedule.every().friday,
        "saturday":  schedule.every().saturday,
        "sunday":    schedule.every().sunday,
    }

    # Etapa 2: publicar en los dias configurados, con hora individual si existe
    for dia in dias:
        hora_dia = dias_con_hora.get(dia, hora)
        day_map[dia].at(hora_dia).do(publicar_hilos_pendientes)
        print(f"[Scheduler] Publicacion: {dia} a las {hora_dia}")

    # Etapa 1: generar hilos todos los viernes a las 18:00 (fijo)
    schedule.every().friday.at(CONFIG["GENERATION_TIME"]).do(generar_hilos_semana)
    print(f"[Scheduler] Generacion: viernes a las {CONFIG['GENERATION_TIME']} (fijo)")

    print("\nScheduler activo — CUAN Arquitectura\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


# -----------------------------------------
# ENTRY POINT
# -----------------------------------------
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if not args:
        iniciar_scheduler()
    elif args[0] == "--ahora":
        publicar_historia_automatica()
    elif args[0] == "--generar":
        generar_hilos_semana()
    elif args[0] == "--publicar":
        publicar_hilos_pendientes()
    else:
        print(f"Argumento desconocido: {args[0]}")
        print("Uso: python main.py [--ahora | --generar | --publicar]")
