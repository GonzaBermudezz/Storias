-- a44: per-client Google Drive connection via OAuth, replacing the need to
-- manually share a Drive folder with the shared service account's email.
-- The employee clicks "Conectar Drive" (see portal.py::start_drive_oauth),
-- signs in with the Google account that owns the client's Drive, and grants
-- read-only access — we store the resulting refresh token, encrypted the
-- same way meta_access_token_encrypted already is (app/services/encryption.py).
-- Nullable/additive: a client with no row here still works exactly as
-- before, through the shared service account in app/services/drive.py.
alter table public.clients
  add column if not exists drive_refresh_token_encrypted text,
  add column if not exists drive_connected_email text;
