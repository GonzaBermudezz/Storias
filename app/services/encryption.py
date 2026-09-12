"""
Cifrado simétrico para datos sensibles guardados en la DB.

Se usa para:
  - Access tokens de Meta/Instagram de cada cliente
  - Números de WhatsApp (si se decide guardarlos)
  - Cualquier secreto por-cliente que no puede vivir en .env

La clave de cifrado (ENCRYPTION_KEY) vive en el .env y NUNCA en la DB.
Así, incluso si alguien roba el dump de Supabase, los secretos son ilegibles.
"""
from __future__ import annotations
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Cifra un string y devuelve el token cifrado (safe para guardar en DB)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Descifra un token. Lanza ValueError si el token es inválido o fue alterado."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Token de cifrado inválido o alterado") from e


def encrypt_if_not_none(value: str | None) -> str | None:
    return encrypt(value) if value is not None else None


def decrypt_if_not_none(value: str | None) -> str | None:
    return decrypt(value) if value is not None else None
