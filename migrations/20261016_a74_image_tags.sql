-- a74: qué muestra cada foto de Drive (¿hay una persona? ¿qué tipo de escena?).
-- La carga la app a medida que genera hilos; es solo lectura de contenido y se
-- puede borrar entera sin perder nada (se vuelve a etiquetar sola).
begin;

create table if not exists public.image_tags (
  client_id uuid not null references public.clients(id) on delete cascade,
  drive_file_id text not null,
  has_person boolean not null,
  scene text not null,
  tagged_at timestamptz not null default now(),
  primary key (client_id, drive_file_id)
);

alter table public.image_tags enable row level security;

commit;
