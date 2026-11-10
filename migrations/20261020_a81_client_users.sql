-- a81: acceso propio del cliente al panel /app. Cada usuario pertenece a un solo
-- cliente (el email es único en toda la base). El login es por link de un solo
-- uso: en la base se guarda solo el hash sha256 del token, nunca el token.
begin;

create table if not exists public.client_users (
  id uuid primary key default uuid_generate_v4(),
  client_id uuid not null references public.clients(id) on delete cascade,
  email text not null,
  name text,
  role text not null default 'owner' check (role in ('owner', 'editor')),
  active boolean not null default true,
  last_login_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists client_users_email_key on public.client_users (lower(email));
create index if not exists client_users_client_idx on public.client_users (client_id);

create table if not exists public.client_login_links (
  id uuid primary key default uuid_generate_v4(),
  client_user_id uuid not null references public.client_users(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_by uuid references public.employees(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists client_login_links_user_idx on public.client_login_links (client_user_id);

alter table public.client_users enable row level security;
alter table public.client_login_links enable row level security;

commit;
