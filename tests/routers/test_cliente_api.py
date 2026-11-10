"""a81: isolation of the client panel API (/app/api). The database runs with the
service role, so these tests are what proves one client can't reach another."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.deps import CLIENT_COOKIE
from app.engine.formatos import FORMATOS
from app.main import app
from app.routers import cliente
from app.routers.auth import _make_jwt


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
    def gte(self, key, value): self.filters.append(("gte", key, value)); return self
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


def user_row(active=True, client_active=True, client_id="c1"):
    return {"id": "u1", "client_id": client_id, "email": "ana@gba.com", "name": "Ana", "role": "owner",
            "active": active, "clients": {"agency_id": "agency-1", "active": client_active}}


def client_token(client_id="c1", sub="u1"):
    return _make_jwt({"sub": sub, "client_id": client_id, "agency_id": "agency-1", "role": "client"})


def employee_token():
    return _make_jwt({"sub": "emp-1", "email": "pm@example.com", "name": "PM",
                      "agency_id": "agency-1", "role": "employee"})


@pytest.fixture
def use_db(monkeypatch):
    def install(responses):
        db = DB(responses)
        monkeypatch.setattr("app.deps.get_admin_client", lambda: db)
        monkeypatch.setattr("app.routers.cliente.get_admin_client", lambda: db)
        return db
    return install


@pytest.fixture
def http():
    return TestClient(app)


def auth(token=None):
    return {CLIENT_COOKIE: token or client_token()}


def story(sid, fecha, order, estado="pendiente", aprobado=False, hora="18:00:00"):
    return {"id": sid, "text": f"texto {sid}", "image_url": f"https://img/{sid}.jpg", "order": order,
            "estado": estado, "aprobado": aprobado, "fecha_publicacion": fecha, "hora_publicacion": hora}


_ENDPOINTS = [
    ("get", "/app/api/me", None),
    ("get", "/app/api/semana", None),
    ("patch", "/app/api/historias/s1/aprobar", {"aprobado": True}),
    ("patch", "/app/api/dias/2026-11-16/aprobar", {"aprobado": True}),
    ("patch", "/app/api/semana", {"enfoque": "x"}),
]


# ── Authentication ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, path, body", _ENDPOINTS)
def test_every_endpoint_requires_a_client_session(use_db, http, method, path, body):
    db = use_db([])
    kwargs = {"json": body} if body is not None else {}
    response = getattr(http, method)(path, **kwargs)
    assert response.status_code == 401
    assert db.calls == []


def test_employee_session_cookie_never_authenticates_the_client_api(use_db, http):
    db = use_db([])
    response = http.get("/app/api/me", cookies={"session": employee_token()})
    assert response.status_code == 401
    assert db.calls == []


def test_employee_jwt_as_bearer_is_forbidden_in_the_client_api(use_db, http):
    use_db([])
    response = http.get("/app/api/me", headers={"Authorization": f"Bearer {employee_token()}"})
    assert response.status_code == 403


def test_client_jwt_as_bearer_is_forbidden_in_the_console(http):
    response = http.get("/portal/me", headers={"Authorization": f"Bearer {client_token()}"})
    assert response.status_code == 403


def test_client_session_cookie_never_authenticates_the_console(http):
    response = http.get("/portal/me", cookies={CLIENT_COOKIE: client_token()})
    assert response.status_code == 401


@pytest.mark.parametrize("row", [user_row(active=False), user_row(client_active=False), None,
                                 user_row(client_id="other")],
                         ids=["user-inactive", "client-inactive", "user-missing", "client-mismatch"])
def test_valid_jwt_is_rejected_when_access_was_removed(use_db, http, row):
    db = use_db([row])
    response = http.get("/app/api/me", cookies=auth())
    assert response.status_code == 401
    assert len(db.calls) == 1  # only the session check, no data read


def test_client_id_comes_from_the_database_row(use_db, http):
    db = use_db([user_row(), {"id": "c1", "name": "GBA", "avatar_url": None, "logo_url": "https://logo"}])
    response = http.get("/app/api/me", cookies=auth())
    assert response.status_code == 200
    assert response.json() == {"id": "u1", "name": "Ana", "email": "ana@gba.com", "role": "owner",
                               "client": {"id": "c1", "name": "GBA", "avatar_url": None, "logo_url": "https://logo"}}
    assert ("eq", "id", "c1") in db.calls[1][3]


# ── Week ────────────────────────────────────────────────────────────────────

def test_week_groups_by_day_hides_cancelled_and_generating_and_computes_day_approval(use_db, http, monkeypatch):
    monkeypatch.setattr(cliente, "_today", lambda: date(2026, 11, 14))
    db = use_db([
        user_row(),
        {"id": "c1", "name": "GBA", "avatar_url": "https://av", "logo_url": None,
         "weekly_focus": "Garmin", "weekly_format": "promo"},
        [
            story("a2", "2026-11-16", 2, aprobado=True),
            story("a1", "2026-11-16", 1, aprobado=True),
            story("x", "2026-11-16", 3, estado="cancelada"),
            story("g", "2026-11-18", 1, estado="generando"),
            story("b1", "2026-11-18", 2, aprobado=False, hora=None),
            story("p1", "2026-11-20", 1, estado="publicado", aprobado=True),
        ],
    ])

    response = http.get("/app/api/semana", cookies=auth())

    assert response.status_code == 200
    body = response.json()
    assert body["cliente"] == {"nombre": "GBA", "avatar_url": "https://av", "logo_url": None}
    assert body["enfoque"] == "Garmin" and body["formato"] == "promo"
    assert {f["key"] for f in body["formatos"]} == set(FORMATOS)
    days = body["dias"]
    assert [d["fecha"] for d in days] == ["2026-11-16", "2026-11-18", "2026-11-20"]
    assert [h["id"] for h in days[0]["historias"]] == ["a1", "a2"]
    assert days[0]["hora"] == "18:00" and days[0]["aprobado"] is True
    assert [h["id"] for h in days[1]["historias"]] == ["b1"]
    assert days[1]["hora"] is None and days[1]["aprobado"] is False
    assert days[2]["historias"][0]["estado"] == "publicado"
    assert days[0]["historias"][0] == {"id": "a1", "orden": 1, "texto": "texto a1", "imagen": "https://img/a1.jpg",
                                       "estado": "pendiente", "aprobado": True}
    stories_query = db.calls[2]
    assert stories_query[0] == "stories"
    assert ("eq", "client_id", "c1") in stories_query[3]
    assert ("gte", "fecha_publicacion", "2026-11-14") in stories_query[3]


def test_week_without_stories_is_empty(use_db, http):
    use_db([user_row(), {"id": "c1", "name": "GBA", "weekly_focus": None, "weekly_format": None}, []])
    body = http.get("/app/api/semana", cookies=auth()).json()
    assert body["dias"] == [] and body["enfoque"] is None and body["formato"] is None


# ── Approve one story ───────────────────────────────────────────────────────

def test_approve_story_of_another_client_is_404_without_update(use_db, http):
    db = use_db([user_row(), None])

    response = http.patch("/app/api/historias/s-other/aprobar", json={"aprobado": True}, cookies=auth())

    assert response.status_code == 404
    assert response.json()["detail"] == "No encontrado"
    lookup = db.calls[1]
    assert ("eq", "id", "s-other") in lookup[3] and ("eq", "client_id", "c1") in lookup[3]
    assert db.ops("update") == []


def test_approve_story_updates_only_aprobado_scoped_to_the_session_client(use_db, http):
    db = use_db([user_row(), {"id": "s1", "estado": "pendiente"}, [{"id": "s1"}]])

    response = http.patch("/app/api/historias/s1/aprobar", json={"aprobado": True}, cookies=auth())

    assert response.status_code == 200
    assert response.json() == {"id": "s1", "aprobado": True}
    [(table, _, payload, filters)] = db.ops("update")
    assert table == "stories" and payload == {"aprobado": True}
    assert ("eq", "client_id", "c1") in filters


@pytest.mark.parametrize("estado", ["generando", "publicando", "publicado", "cancelada"])
def test_approve_locked_story_is_409(use_db, http, estado):
    db = use_db([user_row(), {"id": "s1", "estado": estado}])
    response = http.patch("/app/api/historias/s1/aprobar", json={"aprobado": False}, cookies=auth())
    assert response.status_code == 409
    assert db.ops("update") == []


def test_client_id_in_body_or_query_is_ignored(use_db, http):
    db = use_db([user_row(), {"id": "s1", "estado": "pendiente"}, [{"id": "s1"}]])

    response = http.patch("/app/api/historias/s1/aprobar?client_id=other",
                          json={"aprobado": True, "client_id": "other"}, cookies=auth())

    assert response.status_code == 200
    assert "other" not in repr(db.calls)
    assert all(("eq", "client_id", "c1") in call[3] for call in db.calls[1:])


# ── Approve a whole day ─────────────────────────────────────────────────────

def test_approve_day_without_own_stories_is_404(use_db, http):
    db = use_db([user_row(), []])
    response = http.patch("/app/api/dias/2026-11-16/aprobar", json={"aprobado": True}, cookies=auth())
    assert response.status_code == 404
    assert ("eq", "client_id", "c1") in db.calls[1][3]
    assert db.ops("update") == []


def test_approve_day_with_only_locked_stories_is_404(use_db, http):
    db = use_db([user_row(), [{"id": "p1", "estado": "publicado"}]])
    response = http.patch("/app/api/dias/2026-11-16/aprobar", json={"aprobado": True}, cookies=auth())
    assert response.status_code == 404
    assert db.ops("update") == []


def test_approve_day_updates_active_stories_in_one_call(use_db, http):
    db = use_db([user_row(), [{"id": "a", "estado": "pendiente"}, {"id": "b", "estado": None},
                              {"id": "p", "estado": "publicado"}], [{"id": "a"}, {"id": "b"}]])

    response = http.patch("/app/api/dias/2026-11-16/aprobar?client_id=other",
                          json={"aprobado": True, "client_id": "other"}, cookies=auth())

    assert response.status_code == 200
    assert response.json() == {"fecha": "2026-11-16", "aprobado": True, "cantidad": 2}
    [(table, _, payload, filters)] = db.ops("update")
    assert table == "stories" and payload == {"aprobado": True}
    assert ("in", "id", ["a", "b"]) in filters and ("eq", "client_id", "c1") in filters
    assert ("eq", "fecha_publicacion", "2026-11-16") in db.calls[1][3]


def test_approve_day_with_invalid_date_is_422(use_db, http):
    use_db([user_row()])
    response = http.patch("/app/api/dias/no-es-fecha/aprobar", json={"aprobado": True}, cookies=auth())
    assert response.status_code == 422


# ── Weekly focus and format ─────────────────────────────────────────────────

def test_patch_week_updates_only_the_session_client(use_db, http):
    db = use_db([user_row(), [{"id": "c1", "weekly_focus": "Llegó el Garmin", "weekly_format": "promo"}]])

    response = http.patch("/app/api/semana", json={"enfoque": "  Llegó el Garmin  ", "formato": "promo",
                                                   "client_id": "other"}, cookies=auth())

    assert response.status_code == 200
    assert response.json() == {"enfoque": "Llegó el Garmin", "formato": "promo"}
    [(table, _, payload, filters)] = db.ops("update")
    assert table == "clients"
    assert payload == {"weekly_focus": "Llegó el Garmin", "weekly_format": "promo"}
    assert filters == [("eq", "id", "c1")]


def test_patch_week_empty_focus_becomes_null_and_format_untouched(use_db, http):
    db = use_db([user_row(), [{"id": "c1", "weekly_focus": None, "weekly_format": "promo"}]])
    response = http.patch("/app/api/semana", json={"enfoque": "   "}, cookies=auth())
    assert response.status_code == 200
    assert db.ops("update")[0][2] == {"weekly_focus": None}


def test_patch_week_clears_format_with_null(use_db, http):
    db = use_db([user_row(), [{"id": "c1", "weekly_focus": None, "weekly_format": None}]])
    response = http.patch("/app/api/semana", json={"formato": None}, cookies=auth())
    assert response.status_code == 200
    assert db.ops("update")[0][2] == {"weekly_format": None}


@pytest.mark.parametrize("body", [{"enfoque": "x" * 201}, {"formato": "inventado"}])
def test_patch_week_rejects_long_focus_and_unknown_format(use_db, http, body):
    db = use_db([user_row()])
    response = http.patch("/app/api/semana", json=body, cookies=auth())
    assert response.status_code == 422
    assert db.ops("update") == []

