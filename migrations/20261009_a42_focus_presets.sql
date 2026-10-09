-- a42: reusable "enfoque" presets per agency — a named prompt template
-- (e.g. "Venta" -> "destacá la promo de la semana...") employees pick from
-- a dropdown instead of typing weekly_focus free text by hand. Selecting
-- one just copies its description into clients.weekly_focus (same column,
-- same one-shot consume-after-generation behavior persist_generated_thread
-- already has — see 20260922_a5_fix_database_invariants.sql), so this table
-- only adds a reusable, named SOURCE for that text; nothing about how it's
-- consumed changes, and nothing references a preset by id, so deleting one
-- can never orphan a client.
create table if not exists public.focus_presets (
  id uuid primary key default uuid_generate_v4(),
  agency_id uuid not null references public.agencies(id) on delete cascade,
  name text not null,
  description text not null,
  created_at timestamptz not null default now(),
  unique (agency_id, name)
);
