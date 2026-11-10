"""Access for a client's own users to the /app panel (a81).

Pure functions over the database client, same style as content_jobs: they
receive `db` and never read the request. A login link is a random token the
team sends to the client (WhatsApp, for example); only its sha256 hash is
stored, so the clear token never lives in the database or in a log line.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.config import get_settings


class EmailTakenError(Exception):
    """The email already gives access to another client."""


_USER_FIELDS = "id,client_id,email,name,role,active"
# Same select for the session check (deps.require_client) and for redeeming a
# link: the user row plus its client's agency and active flag in one call.
_USER_WITH_CLIENT = _USER_FIELDS + ",clients(agency_id,active)"


def normalize_email(email: str | None) -> str:
    value = (email or "").strip().lower()
    local, sep, domain = value.partition("@")
    if not sep or not local or "@" in domain or "." not in domain \
            or domain.startswith(".") or domain.endswith(".") or any(ch.isspace() for ch in value):
        raise ValueError("Email inválido")
    return value


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _client_of(row: dict) -> dict:
    embedded = row.get("clients")
    if isinstance(embedded, list):
        embedded = embedded[0] if embedded else None
    return embedded or {}


def fetch_active_client_user(db, user_id: str) -> dict | None:
    """The user and its client, only while BOTH are active. Returns a flat dict
    with the client's agency_id; None for missing or deactivated access."""
    if not user_id:
        return None
    result = db.table("client_users").select(_USER_WITH_CLIENT).eq("id", user_id).maybe_single().execute()
    row = result.data if result else None
    if not row or not row.get("active"):
        return None
    client = _client_of(row)
    if not client.get("active") or not client.get("agency_id"):
        return None
    return {
        "id": row["id"], "client_id": row["client_id"], "agency_id": client["agency_id"],
        "email": row["email"], "name": row.get("name"), "role": row.get("role") or "owner",
    }


def get_or_create_client_user(db, client_id: str, email: str, name: str | None = None) -> dict:
    email = normalize_email(email)
    name = (name or "").strip() or None
    rows = db.table("client_users").select(_USER_FIELDS).eq("email", email).limit(1).execute().data or []
    if rows:
        user = rows[0]
        if user["client_id"] != client_id:
            raise EmailTakenError(email)
        changes = {}
        if not user.get("active"):
            changes["active"] = True
        if name and name != user.get("name"):
            changes["name"] = name
        if changes:
            updated = db.table("client_users").update(changes).eq("id", user["id"]).eq(
                "client_id", client_id
            ).execute().data
            user = updated[0] if updated else {**user, **changes}
        return user
    created = db.table("client_users").insert({
        "client_id": client_id, "email": email, "name": name, "role": "owner",
    }).execute().data
    return created[0]


def create_login_link(db, client_user_id: str, created_by: str | None,
                      now: datetime | None = None, days: int | None = None) -> tuple[str, datetime]:
    """Returns (clear token, expires_at). The clear token only travels back to
    the caller, who turns it into the URL; the table keeps its hash."""
    days = get_settings().client_link_days if days is None else days
    token = secrets.token_urlsafe(32)
    expires_at = _now(now) + timedelta(days=days)
    db.table("client_login_links").insert({
        "client_user_id": client_user_id,
        "token_hash": hash_token(token),
        "expires_at": expires_at.isoformat(),
        "created_by": created_by,
    }).execute()
    return token, expires_at


def redeem_login_link(db, token: str | None, now: datetime | None = None) -> dict | None:
    """The user behind a valid link, spending it; None for every failure
    (unknown, used, expired, user or client inactive) so callers can't tell why."""
    if not token or len(token) > 200:
        return None
    now = _now(now)
    token_hash = hash_token(token)
    rows = db.table("client_login_links").select("id,client_user_id,expires_at,used_at").eq(
        "token_hash", token_hash
    ).limit(1).execute().data or []
    if not rows:
        return None
    link = rows[0]
    expires_at = _parse_ts(link.get("expires_at"))
    if link.get("used_at") or not expires_at or expires_at <= now:
        return None
    user = fetch_active_client_user(db, link["client_user_id"])
    if not user:
        return None
    # Conditional update = the atomic claim: of two simultaneous redeems only
    # the one whose update still finds used_at null gets a row back.
    claimed = db.table("client_login_links").update({"used_at": now.isoformat()}).eq(
        "token_hash", token_hash
    ).is_("used_at", "null").gt("expires_at", now.isoformat()).execute().data
    if not claimed:
        return None
    db.table("client_users").update({"last_login_at": now.isoformat()}).eq("id", user["id"]).execute()
    return user


def list_client_users(db, client_id: str) -> list[dict]:
    return db.table("client_users").select("id,email,name,active,last_login_at").eq(
        "client_id", client_id
    ).order("created_at").execute().data or []


def deactivate_client_user(db, client_id: str, user_id: str, now: datetime | None = None) -> bool:
    """Cuts access at once (every request re-checks `active`). Also spends the
    user's pending links, so re-granting access later never revives a link
    that was already sent around."""
    updated = db.table("client_users").update({"active": False}).eq("id", user_id).eq(
        "client_id", client_id
    ).execute().data
    if not updated:
        return False
    db.table("client_login_links").update({"used_at": _now(now).isoformat()}).eq(
        "client_user_id", user_id
    ).is_("used_at", "null").execute()
    return True
