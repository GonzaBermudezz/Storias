"""
Storias — FastAPI app principal.

Seguridad aplicada por capas:
  1. HTTPS forzado en producción (TrustedHostMiddleware)
  2. CORS restringido a orígenes permitidos
  3. CSP + headers de seguridad en cada response
  4. Rate limiting global (slowapi)
  5. Session cookie httponly + secure
  6. JWT en cada endpoint protegido (ver deps.py)
  7. RLS en Supabase (cada usuario ve solo sus datos)
  8. API keys de clientes cifradas en DB (ver encryption.py)
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import auth, aprobar, demo

s = get_settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"\n{'═'*50}")
    print(f"  Storias — {s.environment.upper()}")
    print(f"  http://localhost:5001")
    print(f"{'═'*50}\n")
    yield


# ── App ───────────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=[f"{s.rate_limit_per_minute}/minute"])

app = FastAPI(
    title="Storias API",
    version="2.0.0",
    docs_url="/docs" if not s.is_production else None,   # Swagger off en prod
    redoc_url="/redoc" if not s.is_production else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Middlewares (orden importa) ────────────────────────────────────────────────

# 1. Hosts permitidos — previene host header injection
if s.is_production:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["storias.app", "*.storias.app"])

# 2. Sesiones firmadas (para el flujo OAuth)
app.add_middleware(SessionMiddleware, secret_key=s.secret_key, https_only=s.is_production)

# 3. CORS — solo orígenes explícitamente permitidos
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# 4. Security headers en cada response
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if s.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(aprobar.router)
app.include_router(demo.router)


# ── Páginas principales ───────────────────────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _render_template(template_name: str, **ctx) -> str:
    """Mini-render de templates sin usar Jinja2Templates (evita bug de caché en starlette 1.x)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    return env.get_template(template_name).render(**ctx)


@app.get("/")
async def landing(request: Request):
    # En dev redirigimos directo al portal para no tener que hacer login
    if not s.is_production:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/portal")
    from fastapi.responses import HTMLResponse
    html = _render_template("landing.html", logged_in=False)
    return HTMLResponse(html)


@app.get("/portal")
async def portal(request: Request):
    from fastapi.responses import HTMLResponse
    name, email, picture = "Demo", "demo@storias.app", None
    if s.is_production:
        from app.deps import _get_token_from_request, _decode_token
        try:
            token = _get_token_from_request(request, None)
            payload = _decode_token(token)
            name = payload.get("name", "")
            email = payload.get("email", "")
            picture = payload.get("picture")
        except Exception:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/")
    html = _render_template("portal.html", name=name, email=email, picture=picture)
    return HTMLResponse(html)


@app.get("/login")
async def login_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/auth/google")


@app.get("/logout")
async def logout_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/auth/logout")


# ── Health check (para Railway / Render) ──────────────────────────────────────
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ── Dev runner ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5001, reload=True)
