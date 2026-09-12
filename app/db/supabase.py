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
from supabase import create_client, Client
from app.config import get_settings


@lru_cache
def get_anon_client() -> Client:
    """Cliente con la clave pública. Respeta Row Level Security."""
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_anon_key)


@lru_cache
def get_admin_client() -> Client:
    """
    Cliente con service_role key. Bypasea RLS.
    Usar SOLO en operaciones de servidor confiables.
    NUNCA pasar este cliente a código que el usuario pueda influenciar.
    """
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)
