"""
Storias — SaaS de automatización de Instagram Stories
Puerto: 5001

Rutas:
  /          → Landing pública
  /login     → Inicia OAuth con Google
  /callback  → Google redirige acá
  /portal    → Selector de opciones (requiere login)
  /logout    → Cierra sesión
"""

from __future__ import annotations
import os
from pathlib import Path
from flask import Flask, redirect, request, session, url_for, render_template
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

load_dotenv(Path(__file__).parent.parent / "testFixed" / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "storias-dev-key")
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id":     os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:5001/callback"],
    }
}

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def logged_in():
    return bool(session.get("email"))


# ─── Rutas ────────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    return render_template("landing.html", logged_in=logged_in(),
                           email=session.get("email"),
                           name=session.get("name"))


@app.route("/login")
def login():
    import secrets, hashlib, base64

    # Generamos PKCE manualmente para controlarlo
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES,
                                   redirect_uri="http://localhost:5001/callback")
    auth_url, state = flow.authorization_url(
        prompt="select_account",
        access_type="offline",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    session["oauth_state"]    = state
    session["code_verifier"]  = code_verifier
    return redirect(auth_url)


@app.route("/callback")
def callback():
    import google.oauth2.id_token
    import google.auth.transport.requests
    import requests as req_lib

    code = request.args.get("code")
    if not code:
        return "Error: no se recibió el código de autorización.", 400

    token_resp = req_lib.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          code,
            "client_id":     os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri":  "http://localhost:5001/callback",
            "grant_type":    "authorization_code",
            "code_verifier": session.get("code_verifier", ""),
        },
        timeout=10,
    )
    token_data = token_resp.json()

    if "error" in token_data:
        return f"Error de Google: {token_data}", 400

    id_info = google.oauth2.id_token.verify_oauth2_token(
        token_data["id_token"],
        google.auth.transport.requests.Request(),
        os.getenv("GOOGLE_CLIENT_ID"),
    )
    session["email"]   = id_info.get("email")
    session["name"]    = id_info.get("name", "").split()[0]
    session["picture"] = id_info.get("picture", "")
    return redirect(url_for("portal"))


@app.route("/portal")
def portal():
    if not logged_in():
        return redirect(url_for("landing"))
    return render_template("portal.html",
                           name=session.get("name"),
                           email=session.get("email"),
                           picture=session.get("picture", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


SCRATCHPAD = Path(
    r"C:\Users\avaca\AppData\Local\Temp\claude"
    r"\C--Users-avaca-OneDrive-Escritorio-web-BotPublicidad-testFixed"
    r"\ccfa3ee6-7a96-4dc0-8a09-74d111836b60\scratchpad"
)

DEMOS = {
    "opcion1": "opcion1-cliente.html",
    "opcion2": "opcion2-empleados.html",
    "opcion3": "opcion3-whitelabel.html",
}


@app.route("/aprobar/<token>")
def aprobar(token):
    """Página de aprobación que recibe el cliente via WhatsApp link. No requiere login."""
    from flask import send_from_directory
    p = SCRATCHPAD / "aprobar.html"
    if not p.exists():
        return "Página de aprobación no encontrada", 404
    return send_from_directory(str(SCRATCHPAD), "aprobar.html")


@app.route("/demo/<opcion>")
def demo(opcion):
    if not logged_in():
        return redirect(url_for("landing"))
    filename = DEMOS.get(opcion)
    if not filename:
        return "Demo no encontrada", 404
    p = SCRATCHPAD / filename
    if not p.exists():
        return f"Archivo {filename} no encontrado en scratchpad", 404
    from flask import send_from_directory
    return send_from_directory(str(SCRATCHPAD), filename)


if __name__ == "__main__":
    print("\n" + "═"*46)
    print("  Storias — http://localhost:5001")
    print("═"*46 + "\n")
    app.run(port=5001, debug=True)
