"""
Clientes de Supabase.

Hay DOS clientes con permisos distintos:
- anon_client  → permisos de usuario anónimo (respeta RLS)
- admin_client → service_role, bypasea RLS. Solo para operaciones de backend
                 que necesitan acceso total (ej: crear tokens, jobs de Celery).
                 NUNCA exponer al frontend.
"""
from __future__ import annotations

from functools import lru_cache

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import get_settings


_POSTGREST_TIMEOUT_SECONDS = 120.0
_CONNECT_TIMEOUT_SECONDS = 10.0
_managed_http_clients: list[httpx.Client] = []


def _create_client(url: str, key: str) -> Client:
    """Construye un cliente estable para servidores con conexiones persistentes."""
    http_client = httpx.Client(
        http2=False,
        timeout=httpx.Timeout(
            _POSTGREST_TIMEOUT_SECONDS,
            connect=_CONNECT_TIMEOUT_SECONDS,
        ),
    )
    options = SyncClientOptions(httpx_client=http_client)
    try:
        client = create_client(url, key, options=options)
    except Exception:
        http_client.close()
        raise
    _managed_http_clients.append(http_client)
    return client


def close_supabase_clients() -> None:
    """Cierra los transports compartidos al apagar el proceso."""
    for http_client in _managed_http_clients:
        http_client.close()
    _managed_http_clients.clear()
    get_anon_client.cache_clear()
    get_admin_client.cache_clear()


@lru_cache
def get_anon_client() -> Client:
    """Cliente con la clave pública. Respeta Row Level Security."""
    s = get_settings()
    return _create_client(s.supabase_url, s.supabase_anon_key)


@lru_cache
def get_admin_client() -> Client:
    """
    Cliente con service_role key. Bypasea RLS.
    Usar SOLO en operaciones de servidor confiables.
    NUNCA pasar este cliente a código que el usuario pueda influenciar.
    """
    s = get_settings()
    return _create_client(s.supabase_url, s.supabase_service_role_key)
