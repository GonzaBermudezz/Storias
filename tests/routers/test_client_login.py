"""a81: the client's login link (/entrar) and the panel page (/app)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import CLIENT_COOKIE
from app.main import app
from app.routers.auth import _make_jwt

_INVALID = "Este link venció o ya se usó. Pedile uno nuevo a quien te lo mandó."


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.single = db, table, False

    def select(self, *args, **kwargs): return self
    def eq(self, *args): return self
    def maybe_single(self): self.single = True; return self

    def execute(self):
        self.db.calls.append(self.table)
        value = self.db.responses.pop(0)
        return None if self.single and value is None else Result(value)


class DB:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def table(self, name):
        return Query(self, name)


def user_row(active=True):
    return {"id": "u1", "client_id": "c1", "email": "ana@gba.com", "name": "Ana", "role": "owner",
            "active": active, "clients": {"agency_id": "agency-1", "active": True}}


def client_token():
    return _make_jwt({"sub": "u1", "client_id": "c1", "agency_id": "agency-1", "role": "client"})


@pytest.fixture
def http():
    return TestClient(app)


def _no_db():
    raise AssertionError("the link preview must not touch the database")


def test_opening_the_link_shows_the_button_and_never_spends_it(http, monkeypatch):
    redeemed = []
    monkeypatch.setattr("app.services.client_access.redeem_login_link", lambda *a, **k: redeemed.append(a))
    monkeypatch.setattr("app.db.supabase.get_admin_client", _no_db)

    response = http.get("/entrar?token=abc123")

    assert response.status_code == 200
    assert "Entrar a mi panel" in response.text
    assert 'method="post" action="/entrar"' in response.text
    assert 'name="token" value="abc123"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert redeemed == []


def test_link_preview_escapes_the_token(http):
    response = http.get('/entrar?token="><script>x</script>')
    assert "<script>x</script>" not in response.text


def test_valid_link_sets_client_cookie_and_redirects_to_app(http, monkeypatch):
    seen = []

    def redeem(db, token, now=None):
        seen.append(token)
        return {"id": "u1", "client_id": "c1", "agency_id": "agency-1", "email": "ana@gba.com",
                "name": "Ana", "role": "owner"}

    monkeypatch.setattr("app.services.client_access.redeem_login_link", redeem)
    monkeypatch.setattr("app.db.supabase.get_admin_client", lambda: object())

    response = http.post("/entrar", data={"token": "abc123"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert seen == ["abc123"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{CLIENT_COOKIE}=")
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    assert "session=" not in cookie.replace(f"{CLIENT_COOKIE}=", "")


def test_invalid_link_shows_the_generic_message(http, monkeypatch):
    monkeypatch.setattr("app.services.client_access.redeem_login_link", lambda *a, **k: None)
    monkeypatch.setattr("app.db.supabase.get_admin_client", lambda: object())

    response = http.post("/entrar", data={"token": "nope"}, follow_redirects=False)

    assert response.status_code == 400
    assert _INVALID in response.text
    assert "set-cookie" not in response.headers


def test_app_without_session_asks_to_use_the_link(http):
    response = http.get("/app")
    assert response.status_code == 401
    assert "Entrá desde el link que te mandamos" in response.text


def test_app_ignores_the_employee_session(http):
    employee = _make_jwt({"sub": "emp-1", "email": "pm@example.com", "name": "PM",
                          "agency_id": "agency-1", "role": "employee"})
    response = http.get("/app", cookies={"session": employee})
    assert response.status_code == 401


def test_app_with_session_renders_shell_without_client_data(http, monkeypatch):
    db = DB([user_row()])
    monkeypatch.setattr("app.deps.get_admin_client", lambda: db)

    response = http.get("/app", cookies={CLIENT_COOKIE: client_token()})

    assert response.status_code == 200
    assert "Tu semana" in response.text
    assert '<script src="/static/app.js" defer></script>' in response.text
    for secret in ("ana@gba.com", "u1", "c1", "agency-1"):
        assert f'"{secret}"' not in response.text and f">{secret}<" not in response.text
    assert db.calls == ["client_users"]


def test_app_with_revoked_access_asks_to_use_the_link(http, monkeypatch):
    monkeypatch.setattr("app.deps.get_admin_client", lambda: DB([user_row(active=False)]))
    response = http.get("/app", cookies={CLIENT_COOKIE: client_token()})
    assert response.status_code == 401


def test_logout_clears_the_client_cookie(http):
    response = http.get("/app/salir", cookies={CLIENT_COOKIE: client_token()}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert response.headers["set-cookie"].startswith(f'{CLIENT_COOKIE}=""')


def test_client_session_never_opens_the_console(http):
    response = http.get("/portal", cookies={CLIENT_COOKIE: client_token()}, follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/login"


@pytest.mark.parametrize("method, path", [("post", "/auth/magic-link"), ("get", "/auth/magic-callback")])
def test_supabase_magic_link_routes_are_gone(http, method, path):
    assert getattr(http, method)(path).status_code in (404, 405)


# ── Page and frontend guards ────────────────────────────────────────────────

def test_app_page_has_no_text_below_11px():
    import re

    page = Path("templates/app.html").read_text(encoding="utf-8")
    sizes = [float(size) for size in re.findall(r"font-size:([0-9.]+)px", page)]
    assert sizes and min(sizes) >= 11


def test_client_frontend_never_sends_client_id_and_escapes_api_data():
    import re

    source = Path("static/app.js").read_text(encoding="utf-8")
    assert "client_id" not in source
    assert "localStorage" not in source and "sessionStorage" not in source
    # Every interpolation inside markup goes through escapeHtml, except the
    # constant icon and the render helpers that escape their own fields.
    allowed = ("escapeHtml(", "CHECK_ICON", "image", "action", "button", "day.historias.map(storyHtml)")
    for expr in re.findall(r"\$\{([^}]*)\}", source):
        expr = expr.strip()
        if expr.startswith(("weekday", "rest", "formatDay(", "day.hora", "pending", "error ?", "path",
                            "encodeURIComponent(")):
            continue  # plain text / URL parts, never assigned as markup
        assert expr.startswith(allowed), expr


def test_new_templates_have_no_stray_jinja_in_css():
    for name in ("app.html", "entrar.html"):
        page = Path("templates", name).read_text(encoding="utf-8")
        style = page.split("<style>", 1)[1].split("</style>", 1)[0]
        assert "{{" not in style and "{%" not in style and "{#" not in style
    assert "{{" not in Path("templates/app.html").read_text(encoding="utf-8")
