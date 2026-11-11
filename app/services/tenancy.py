from __future__ import annotations

from app.services.errors import ServiceError

CLIENT_DETAIL = (
    "id,agency_id,name,business_description,weekly_focus,"
    "weekly_focus_expires_at,tone_examples,topics,drive_folder_id,logo_url,"
    "avatar_url,avatar_public_id,instagram_profile_url,whatsapp_contact,"
    "contact_email,calendly_link,prob_link,generation_error,"
    "generation_error_at,publish_days,"
    "publish_together,font_choice,drive_connected_email,weekly_format,voice_style,learned_edits,website_url"
)
# PostgREST silently truncates a response at its max-rows (1000 by default),
# so bulk reads go through paged queries.
PAGE_SIZE = 500
_SECRET_COLUMNS = ("drive_refresh_token_encrypted", "meta_access_token_encrypted")


def client_or_error(db, client_id: str, agency_id: str, select_fields: str = CLIENT_DETAIL) -> dict:
    result = db.table("clients").select(select_fields).eq("id", client_id).maybe_single().execute()
    # postgrest-py's maybe_single() returns None outright (not a response with
    # data=None) when zero rows match — never assume `result` itself is set.
    if not result or not result.data:
        raise ServiceError(status_code=404, detail="Cliente no encontrado")
    if result.data["agency_id"] != agency_id:
        raise ServiceError(status_code=403, detail="Cliente fuera de la agencia")
    return result.data


def without_secrets(row: dict) -> dict:
    """Drop encrypted credential columns before a clients row goes back to the browser."""
    return {key: value for key, value in row.items() if key not in _SECRET_COLUMNS}


def select_all(build_query):
    """Read every row of a query, page by page. `build_query` must return a
    FRESH, ordered query each call (a builder can't be reused after execute)."""
    rows, offset = [], 0
    while True:
        page = build_query().range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE
