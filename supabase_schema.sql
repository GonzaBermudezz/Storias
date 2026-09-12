-- ═══════════════════════════════════════════════════════════════════════════
-- Storias — Schema de Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- Extensiones
create extension if not exists "uuid-ossp";

-- ── AGENCIES ─────────────────────────────────────────────────────────────────
create table agencies (
  id          uuid primary key default uuid_generate_v4(),
  name        text not null,
  slug        text unique not null,   -- "argo-media" → subdomain futuro
  plan        text not null default 'basic' check (plan in ('basic','pro','enterprise')),
  created_at  timestamptz default now()
);

-- ── EMPLOYEES ────────────────────────────────────────────────────────────────
create table employees (
  id          uuid primary key default uuid_generate_v4(),
  agency_id   uuid not null references agencies(id) on delete cascade,
  email       text unique not null,
  name        text not null,
  role        text not null default 'employee' check (role in ('employee','admin')),
  active      boolean default true,
  created_at  timestamptz default now()
);

-- ── CLIENTS ──────────────────────────────────────────────────────────────────
-- Los datos sensibles (tokens de Meta, número de WA) van CIFRADOS.
-- La clave de cifrado vive en el .env, nunca en la DB.
create table clients (
  id                              uuid primary key default uuid_generate_v4(),
  agency_id                       uuid not null references agencies(id) on delete cascade,
  name                            text not null,           -- "CUAN Arquitectura"
  contact_name                    text,                    -- "Lucía Cuan"
  contact_email                   text,                    -- para magic link opción 1
  wa_phone_encrypted              text,                    -- CIFRADO: "5491112345678"
  business_description            text,                    -- prompt para la IA
  instagram_account_id            text,                    -- ID de cuenta de IG
  meta_access_token_encrypted     text,                    -- CIFRADO: token de Meta API
  logo_url                        text,                    -- Supabase Storage
  active                          boolean default true,
  option                          text default 'opcion2' check (option in ('opcion1','opcion2','opcion3')),
  created_at                      timestamptz default now()
);

-- ── STORY GROUPS ─────────────────────────────────────────────────────────────
create table story_groups (
  id              uuid primary key default uuid_generate_v4(),
  client_id       uuid not null references clients(id) on delete cascade,
  agency_id       uuid not null references agencies(id),
  scheduled_date  date not null,
  scheduled_time  time not null default '09:00:00',
  status          text not null default 'draft'
                  check (status in ('draft','pending_approval','approved','published','failed')),
  approved_at     timestamptz,
  published_at    timestamptz,
  created_by      uuid references employees(id),
  created_at      timestamptz default now()
);

-- ── STORIES (historias individuales) ─────────────────────────────────────────
create table stories (
  id              uuid primary key default uuid_generate_v4(),
  story_group_id  uuid not null references story_groups(id) on delete cascade,
  client_id       uuid not null references clients(id),
  "order"         int not null check ("order" between 1 and 10),
  text            text not null,
  image_url       text,           -- Supabase Storage o Google Drive
  ig_media_id     text,           -- ID devuelto por Meta después de publicar
  approved_at     timestamptz,
  created_at      timestamptz default now(),
  unique(story_group_id, "order")
);

-- ── APPROVAL TOKENS (opción 3) ────────────────────────────────────────────────
create table approval_tokens (
  id                  uuid primary key default uuid_generate_v4(),
  token               uuid unique not null default uuid_generate_v4(),
  story_group_id      uuid not null references story_groups(id) on delete cascade,
  client_id           uuid not null references clients(id),
  agency_id           uuid not null references agencies(id),
  created_by          uuid references employees(id),
  expires_at          timestamptz not null,
  used_at             timestamptz,
  invalidated_at      timestamptz,
  approved_story_ids  uuid[],
  comentario          text,
  created_at          timestamptz default now()
);

-- ── ÍNDICES ───────────────────────────────────────────────────────────────────
create index on employees(agency_id);
create index on employees(email);
create index on clients(agency_id);
create index on story_groups(client_id);
create index on story_groups(agency_id, status);
create index on stories(story_group_id);
create index on approval_tokens(token);
create index on approval_tokens(story_group_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY — cada fila solo visible para quien corresponde
-- ═══════════════════════════════════════════════════════════════════════════

alter table agencies       enable row level security;
alter table employees      enable row level security;
alter table clients        enable row level security;
alter table story_groups   enable row level security;
alter table stories        enable row level security;
alter table approval_tokens enable row level security;

-- Empleados ven solo su agencia
create policy "employees_own_agency" on employees
  for all using (agency_id = (
    select agency_id from employees where id = auth.uid()
  ));

create policy "clients_own_agency" on clients
  for all using (agency_id = (
    select agency_id from employees where id = auth.uid()
  ));

create policy "story_groups_own_agency" on story_groups
  for all using (agency_id = (
    select agency_id from employees where id = auth.uid()
  ));

create policy "stories_own_agency" on stories
  for all using (client_id in (
    select id from clients where agency_id = (
      select agency_id from employees where id = auth.uid()
    )
  ));

-- Tokens de aprobación: empleados ven los de su agencia,
-- el público (link de WA) accede por el token directamente vía service_role
create policy "approval_tokens_own_agency" on approval_tokens
  for all using (agency_id = (
    select agency_id from employees where id = auth.uid()
  ));
