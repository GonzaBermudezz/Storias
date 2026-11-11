from __future__ import annotations

from app.services.tenancy import client_or_error, without_secrets


def apply_prompt_patch(db, client_id: str, employee_id: str, agency_id: str, supplied: dict) -> dict:
    """Audited update of the brand/prompt fields (business_description, topics,
    tone_examples, weekly_focus…) through the update_client_prompt RPC, which
    also writes the prompt_history rows in the same transaction."""
    current = client_or_error(db, client_id, agency_id)
    changes = {key: value for key, value in supplied.items() if current.get(key) != value}
    if not changes:
        return current

    updated = db.rpc("update_client_prompt", {
        "p_client_id": client_id, "p_employee_id": employee_id,
        "p_agency_id": agency_id, "p_patch": changes,
    }).execute().data
    if isinstance(updated, dict):
        return without_secrets(updated)
    if isinstance(updated, list) and updated:
        return without_secrets(updated[0])
    return {**current, **changes}


def set_weekly_format(db, client_id: str, agency_id: str, weekly_format: str | None) -> dict:
    """Formato de la próxima generación (None = automático). Estilístico, sin auditoría."""
    client_or_error(db, client_id, agency_id)
    updated = db.table("clients").update({"weekly_format": weekly_format}).eq(
        "id", client_id
    ).execute().data
    return without_secrets(updated[0]) if isinstance(updated, list) and updated else {"id": client_id, "weekly_format": weekly_format}
