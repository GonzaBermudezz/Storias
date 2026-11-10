-- a78: formato de la semana. null = automático (el arco de siempre).
-- Se consume en la próxima generación y vuelve a null (ver content_jobs.generate_for_client).
alter table public.clients add column if not exists weekly_format text;
