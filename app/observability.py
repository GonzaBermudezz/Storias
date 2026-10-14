"""Sentry opcional. No hace nada si SENTRY_DSN está vacío.

Los eventos nunca deben llevar secretos: tokens de Meta, claves de Claude,
credenciales de Drive ni datos de clientes. Por eso se desactivan las variables
locales, el cuerpo de los requests, el PII por defecto y los breadcrumbs de
HTTP, y además se limpia todo texto que se parezca a un token antes de enviarlo.
"""
from __future__ import annotations

import os
import re

from app.config import get_settings

# Tokens de query strings, headers Bearer/JWT y tokens de Meta (EAA/EAB).
_TOKEN_RE = re.compile(
    r"(?i)(access_token=)[^&\s\"']+|\b(Bearer)\s+[A-Za-z0-9._~+/=-]+|\b(?:EAA|EAB)[A-Za-z0-9]{20,}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_DROPPED_BREADCRUMBS = {"httplib", "httpx"}
_REDACTED = "[redacted]"


def _scrub_text(value: str) -> str:
    def redact(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}{_REDACTED}"
        if match.group(2):
            return f"{match.group(2)} {_REDACTED}"
        return _REDACTED

    return _TOKEN_RE.sub(redact, value)


def _normalized_key(key: object) -> str:
    if not isinstance(key, str):
        return ""
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized in {
        "token",
        "access_token",
        "meta_access_token",
        "authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
    }:
        return True
    return normalized.endswith((
        "_token", "_api_key", "_apikey", "_secret", "_secret_key", "_password"
    ))


def _scrub(value):
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    return value


def before_send(event, hint):
    return _scrub(event)


def before_breadcrumb(crumb, hint):
    if crumb.get("category") in _DROPPED_BREADCRUMBS:
        return None
    return _scrub(crumb)


def init_sentry(service: str | None = None, settings=None) -> bool:
    """Devuelve True si inicializó Sentry, False si está desactivado."""
    s = settings or get_settings()
    if not s.sentry_dsn:
        return False
    import sentry_sdk

    if sentry_sdk.is_initialized():
        return True
    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.environment,
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
    )
    sentry_sdk.set_tag("service", service or os.getenv("RAILWAY_SERVICE_NAME", "local"))
    return True