-- a43: Postgres never auto-indexes a foreign-key column (only primary keys
-- and UNIQUE constraints get one). These three are filtered on in nearly
-- every portal request — agency_id on almost every /portal/clientes and
-- /portal/empleados call, story_groups.client_id on every single client
-- selection (list_stories, schedule_day, update_day_time, etc.) — and none
-- of them had an index, so every one of those reads was a full table scan.
-- Safe to run any time: purely additive, no data or app-code change.
create index if not exists clients_agency_id_idx on public.clients(agency_id);
create index if not exists employees_agency_id_idx on public.employees(agency_id);
create index if not exists story_groups_client_id_idx on public.story_groups(client_id);
