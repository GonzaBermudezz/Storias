"""
Dependencias de FastAPI (inyección en los endpoints).

Uso típico:
    @router.get("/portal")
    async def portal(user: Employee = Depends(require_employee)):
        ...
"""
from __future__ import annotations
from typing import Annotated
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.config import get_settings
from app.db.supabase import get_admin_client
from app.models.user import Employee, ClientUser
from app.services.client_access import fetch_active_client_user

# Cookie de sesión del panel del cliente (/app). Distinta de `session` (empleados).
CLIENT_COOKIE = "client_session"

_bearer = HTTPBearer(auto_error=False)


def _decode_token(token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=["HS256"])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def _get_token_from_request(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """
    Busca el JWT en:
    1. Header Authorization: Bearer <token>
    2. Cookie 'session'  (para el portal web)
    """
    if creds:
        return creds.credentials
    cookie = request.cookies.get("session")
    if cookie:
        return cookie
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_employee(
    token: Annotated[str, Depends(_get_token_from_request)],
) -> Employee:
    """
    Verifica que el request viene de un empleado de agencia autenticado.
    Inyectar en cualquier endpoint del panel de empleados.
    """
    payload = _decode_token(token)
    if payload.get("role") not in ("employee", "admin"):
        raise HTTPException(status_code=403, detail="Acceso restringido a empleados")
    return Employee(
        id=payload["sub"],
        email=payload["email"],
        name=payload.get("name", ""),
        agency_id=payload["agency_id"],
        role=payload["role"],
    )


async def require_admin(
    employee: Annotated[Employee, Depends(require_employee)],
) -> Employee:
    """Solo admins de agencia."""
    if employee.role != "admin":
        raise HTTPException(status_code=403, detail="Se requiere rol admin")
    return employee


def _get_client_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Token del cliente: header Bearer o cookie `client_session`. Nunca la
    cookie `session` de empleados — una sesión no autentica a la otra."""
    if creds:
        return creds.credentials
    cookie = request.cookies.get(CLIENT_COOKIE)
    if cookie:
        return cookie
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def client_user_from_token(token: str) -> ClientUser:
    """Valida un JWT de cliente contra la base. Lo usan require_client y la
    página GET /app. El client_id sale de la fila de client_users, no del token;
    quitar el acceso (usuario o cliente inactivo) corta la sesión al instante."""
    payload = _decode_token(token)
    if payload.get("role") != "client":
        raise HTTPException(status_code=403, detail="Acceso restringido a clientes")
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = fetch_active_client_user(get_admin_client(), str(payload.get("sub") or ""))
    if not user or user["client_id"] != payload.get("client_id"):
        raise unauthorized
    return ClientUser(**user)


def require_client(
    token: Annotated[str, Depends(_get_client_token)],
) -> ClientUser:
    """Usuario de cliente autenticado — inyectar en los endpoints de /app/api."""
    return client_user_from_token(token)


# Tipos anotados para usar en los endpoints
EmployeeDep = Annotated[Employee, Depends(require_employee)]
AdminDep = Annotated[Employee, Depends(require_admin)]
ClientDep = Annotated[ClientUser, Depends(require_client)]
