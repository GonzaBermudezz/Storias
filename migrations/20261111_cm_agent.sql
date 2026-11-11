-- CM con IA (CLAUDE.md, seccion 16): conversaciones con el equipo. Idempotente.
-- RLS activado y SIN politicas: solo el backend (service role) las lee y escribe,
-- igual que client_users.
begin;

alter table public.agencies add column if not exists cm_enabled boolean not null default false;

create table if not exists public.cm_conversations (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  created_by uuid references public.employees(id) on delete set null,
  title text,
  system_prompt text not null,
  tools_version text not null,
  busy_until timestamptz,
  busy_token uuid,
  user_turns int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  closed_at timestamptz
);

-- Owner of the busy_until lease: renew and release only while the token is still theirs.
alter table public.cm_conversations add column if not exists busy_token uuid;

create table if not exists public.cm_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.cm_conversations(id) on delete cascade,
  client_id uuid not null references public.clients(id) on delete cascade,
  seq int not null,
  role text not null check (role in ('user', 'assistant')),
  kind text not null check (kind in ('mensaje', 'tool_result', 'respuesta')),
  content jsonb not null,
  replay boolean not null default true,
  usage jsonb,
  stop_reason text,
  created_by uuid references public.employees(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (conversation_id, seq)
);

create table if not exists public.cm_pending_actions (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.cm_conversations(id) on delete cascade,
  client_id uuid not null references public.clients(id) on delete cascade,
  tool_use_id text not null,
  kind text not null,
  payload jsonb not null,
  before jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'applying', 'approved', 'discarded', 'stale', 'failed')),
  error text,
  decided_by uuid references public.employees(id) on delete set null,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  unique (conversation_id, tool_use_id)
);

create index if not exists cm_conversations_client_idx on public.cm_conversations (client_id, updated_at desc);
create index if not exists cm_messages_day_idx on public.cm_messages (client_id, created_at) where kind = 'mensaje';
create index if not exists cm_pending_actions_pending_idx on public.cm_pending_actions (client_id) where status = 'pending';
create index if not exists cm_conversations_created_by_idx on public.cm_conversations (created_by);
create index if not exists cm_messages_created_by_idx on public.cm_messages (created_by);
create index if not exists cm_pending_actions_decided_by_idx on public.cm_pending_actions (decided_by);

alter table public.cm_conversations enable row level security;
alter table public.cm_messages enable row level security;
alter table public.cm_pending_actions enable row level security;

commit;
