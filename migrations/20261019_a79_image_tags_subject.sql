-- a79: qué producto se ve en cada foto (texto corto, lo escribe Claude al etiquetar).
-- null = todavía no reconocido; '' = se miró y no hay un producto claro.
begin;

alter table public.image_tags add column if not exists subject text;

commit;
