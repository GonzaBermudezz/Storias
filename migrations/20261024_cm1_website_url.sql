-- Coleccion CM: la web del negocio, para que el onboarding con IA la use sin volver a pedirla.
alter table public.clients add column if not exists website_url text;
