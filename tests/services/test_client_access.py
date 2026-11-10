from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services import client_access as access

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.single = "select", None, [], False

    def select(self, *args, **kwargs): return self
    def eq(self, key, value): self.filters.append(("eq", key, value)); return self
    def is_(self, key, value): self.filters.append(("is", key, value)); return self
    def gt(self, key, value): self.filters.append(("gt", key, value)); return self
    def in_(self, key, values): self.filters.append(("in", key, list(values))); return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def maybe_single(self): self.single = True; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def insert(self, payload): self.op, self.payload = "insert", payload; return self

    def execute(self):
        self.db.calls.append((self.table, self.op, self.payload, self.filters))
        value = self.db.responses.pop(0)
        if self.single and value is None:
            return None
        return Result(value)


class DB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, name):
        return Query(self, name)

    def ops(self, op):
        return [call for call in self.calls if call[1] == op]


def _user(active=True, client_active=True, client_id="c1"):
    return {"id": "u1", "client_id": client_id, "email": "ana@gba.com", "name": "Ana", "role": "owner",
            "active": active, "clients": {"agency_id": "agency-1", "active": client_active}}


def _link(**overrides):
    return {"id": "l1", "client_user_id": "u1", "expires_at": (NOW + timedelta(days=3)).isoformat(),
            "used_at": None, **overrides}


# ── normalize_email / get_or_create_client_user ─────────────────────────────

def test_normalize_email_strips_and_lowercases():
    assert access.normalize_email("  Ana@GBA.com ") == "ana@gba.com"


@pytest.mark.parametrize("bad", ["", None, "ana", "ana@gba", "@gba.com", "ana@.com", "ana@gba.", "a b@gba.com"])
def test_normalize_email_rejects_invalid(bad):
    with pytest.raises(ValueError):
        access.normalize_email(bad)


def test_get_or_create_creates_owner_with_normalized_email():
    db = DB([[], [{"id": "u1", "client_id": "c1", "email": "ana@gba.com", "role": "owner"}]])

    user = access.get_or_create_client_user(db, "c1", "  ANA@gba.com ")

    assert user["id"] == "u1"
    assert db.calls[0][3] == [("eq", "email", "ana@gba.com")]
    assert db.ops("insert")[0][2] == {"client_id": "c1", "email": "ana@gba.com", "name": None, "role": "owner"}


def test_get_or_create_reactivates_inactive_user_of_same_client():
    db = DB([[_user(active=False)], [{**_user(), "active": True}]])

    user = access.get_or_create_client_user(db, "c1", "ana@gba.com")

    assert user["active"] is True
    update = db.ops("update")[0]
    assert update[2] == {"active": True}
    assert ("eq", "client_id", "c1") in update[3]
    assert db.ops("insert") == []


def test_get_or_create_returns_existing_active_user_without_writes():
    db = DB([[_user()]])

    assert access.get_or_create_client_user(db, "c1", "ana@gba.com")["id"] == "u1"
    assert db.ops("update") == [] and db.ops("insert") == []


def test_get_or_create_rejects_email_of_another_client():
    db = DB([[_user(client_id="other")]])

    with pytest.raises(access.EmailTakenError):
        access.get_or_create_client_user(db, "c1", "ana@gba.com")
    assert db.ops("update") == [] and db.ops("insert") == []


# ── create_login_link ───────────────────────────────────────────────────────

def test_create_login_link_stores_only_the_hash():
    db = DB([[{"id": "l1"}]])

    token, expires_at = access.create_login_link(db, "u1", created_by="emp-1", now=NOW, days=7)

    assert len(token) >= 40
    assert expires_at == NOW + timedelta(days=7)
    payload = db.ops("insert")[0][2]
    assert payload["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in repr(payload)
    assert payload["client_user_id"] == "u1" and payload["created_by"] == "emp-1"


def test_create_login_link_tokens_are_unique():
    db = DB([[{}], [{}]])
    first, _ = access.create_login_link(db, "u1", None, now=NOW, days=7)
    second, _ = access.create_login_link(db, "u1", None, now=NOW, days=7)
    assert first != second


# ── redeem_login_link ───────────────────────────────────────────────────────

def test_redeem_valid_link_returns_user_and_marks_used():
    db = DB([[_link()], _user(), [{"id": "l1"}], [{"id": "u1"}]])

    user = access.redeem_login_link(db, "tok", now=NOW)

    assert user == {"id": "u1", "client_id": "c1", "agency_id": "agency-1", "email": "ana@gba.com",
                    "name": "Ana", "role": "owner"}
    claim, last_login = db.ops("update")
    assert claim[0] == "client_login_links" and claim[2] == {"used_at": NOW.isoformat()}
    assert ("eq", "token_hash", access.hash_token("tok")) in claim[3]
    assert ("is", "used_at", "null") in claim[3]
    assert last_login[0] == "client_users" and last_login[2] == {"last_login_at": NOW.isoformat()}


@pytest.mark.parametrize("responses", [
    [[]],                                                         # unknown token
    [[_link(used_at=NOW.isoformat())]],                           # already used
    [[_link(expires_at=(NOW - timedelta(seconds=1)).isoformat())]],  # expired
    [[_link()], _user(active=False)],                             # user deactivated
    [[_link()], _user(client_active=False)],                      # client deactivated
    [[_link()], None],                                            # user deleted
], ids=["unknown", "used", "expired", "user-inactive", "client-inactive", "user-missing"])
def test_redeem_rejects_and_never_spends_invalid_links(responses):
    db = DB(responses)

    assert access.redeem_login_link(db, "tok", now=NOW) is None
    assert db.ops("update") == []


def test_redeem_is_atomic_when_the_conditional_update_finds_no_row():
    # Another redeem spent it between our read and our update.
    db = DB([[_link()], _user(), []])

    assert access.redeem_login_link(db, "tok", now=NOW) is None
    assert [call[0] for call in db.ops("update")] == ["client_login_links"]


@pytest.mark.parametrize("token", ["", None, "x" * 201])
def test_redeem_rejects_empty_or_oversized_tokens_without_queries(token):
    db = DB([])
    assert access.redeem_login_link(db, token, now=NOW) is None
    assert db.calls == []


def test_redeem_never_queries_by_the_clear_token():
    db = DB([[]])
    access.redeem_login_link(db, "secret-token", now=NOW)
    assert "secret-token" not in repr(db.calls)


# ── deactivate_client_user ──────────────────────────────────────────────────

def test_deactivate_filters_by_client_and_spends_pending_links():
    db = DB([[{"id": "u1"}], []])

    assert access.deactivate_client_user(db, "c1", "u1", now=NOW) is True
    user_update, links_update = db.ops("update")
    assert user_update[2] == {"active": False}
    assert ("eq", "id", "u1") in user_update[3] and ("eq", "client_id", "c1") in user_update[3]
    assert links_update[0] == "client_login_links" and ("is", "used_at", "null") in links_update[3]


def test_deactivate_user_of_another_client_returns_false():
    db = DB([[]])
    assert access.deactivate_client_user(db, "c1", "u-other", now=NOW) is False
    assert len(db.ops("update")) == 1
