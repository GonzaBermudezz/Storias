-- a75: id del archivo del logo en Cloudinary, para poder reemplazarlo o quitarlo
-- sin dejar basura (igual que avatar_public_id). Sin logo = historias sin logo.
alter table public.clients add column if not exists logo_public_id text;
