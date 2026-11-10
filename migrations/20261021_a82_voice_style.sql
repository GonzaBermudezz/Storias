-- a82: estilo de voz por cliente (clave de app/engine/voces.py). null = Estándar
-- (el tono de siempre). No se consume: queda fijo hasta que alguien lo cambie.
alter table public.clients add column if not exists voice_style text;
