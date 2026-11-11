-- Fase 4: lo que la IA aprendió de las correcciones del equipo. Cada vez que alguien
-- cambia el texto de una historia se guarda {id, antes, despues, fecha} (las últimas 8,
-- la más nueva primero) y el motor lo suma al prompt. Se puede olvidar desde Marca.
alter table public.clients add column if not exists learned_edits jsonb not null default '[]'::jsonb;
