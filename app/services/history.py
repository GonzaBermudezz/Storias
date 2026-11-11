from __future__ import annotations

from app.services.tenancy import client_or_error


def prompt_history(db, client_id: str, agency_id: str) -> list[dict]:
    client_or_error(db, client_id, agency_id)
    rows = db.table("prompt_history").select(
        "id,field,old_value,new_value,changed_at,changed_by,employees(name)"
    ).eq("client_id", client_id).order("changed_at", desc=True).limit(50).execute().data or []
    for row in rows:
        row["changed_by_name"] = (row.pop("employees", None) or {}).get("name")
    return rows
