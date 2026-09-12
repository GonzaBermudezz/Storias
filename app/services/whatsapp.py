"""
Servicio de WhatsApp via Meta Cloud API.

Los números de WhatsApp de los clientes se guardan CIFRADOS en la DB.
Este servicio los descifra en memoria, los usa, y no los loguea.
"""
from __future__ import annotations
import httpx
from app.config import get_settings
from app.services.encryption import decrypt


async def send_approval_message(
    client_data: dict,
    group_data: dict,
    approval_url: str,
) -> None:
    """Manda el mensaje de aprobación al cliente por WhatsApp."""
    s = get_settings()

    # Descifrar número — existe en memoria solo durante esta función
    wa_phone = decrypt(client_data["wa_phone_encrypted"])
    contact_name = client_data.get("contact_name") or client_data["name"].split()[0]

    fecha = group_data.get("scheduled_date", "próximamente")
    message = (
        f"Hola {contact_name} 👋 "
        f"Tus historias de Instagram para el *{fecha}* están listas.\n\n"
        f"Revisalas y aprobá en 30 segundos 👇\n\n"
        f"{approval_url}\n\n"
        f"_Link válido por 48 hs_"
    )

    if not s.meta_app_id:
        # En desarrollo sin credenciales de Meta, loguear y salir
        print(f"[WA-DEV] Para {wa_phone}: {message}")
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://graph.facebook.com/v21.0/{s.meta_app_id}/messages",
            headers={"Authorization": f"Bearer {s.meta_app_secret}"},
            json={
                "messaging_product": "whatsapp",
                "to": wa_phone,
                "type": "text",
                "text": {"body": message},
            },
            timeout=10,
        )
        resp.raise_for_status()
