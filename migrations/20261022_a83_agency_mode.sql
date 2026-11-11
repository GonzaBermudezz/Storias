-- a83: modo de la agencia. 'multi' = agencia con varios clientes y PMs (la consola de
-- siempre). 'direct' = un negocio que usa Storias solo: una agencia con UN cliente, sin
-- PMs ni empleados para asignar. El modo viaja en el JWT del login (hay que volver a
-- entrar para ver un cambio de modo).
--
-- Alta de un cliente directo (a mano, en el SQL Editor):
--   insert into public.agencies (name, slug, mode) values ('GBA', 'gba', 'direct') returning id;
--   insert into public.employees (agency_id, email, name, role) values ('<id>', 'dueno@gba.com', 'Nombre', 'admin');
--   insert into public.clients (agency_id, name, active) values ('<id>', 'GBA', true);
begin;

alter table public.agencies add column if not exists mode text not null default 'multi';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'agencies_mode_check'
  ) then
    alter table public.agencies add constraint agencies_mode_check check (mode in ('multi', 'direct'));
  end if;
end $$;

commit;
