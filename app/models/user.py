from __future__ import annotations
from pydantic import BaseModel, EmailStr
from typing import Literal


class Employee(BaseModel):
    """Empleado de agencia — accede al panel de gestión."""
    id: str
    email: EmailStr
    name: str
    agency_id: str
    role: Literal["employee", "admin"]
    # a83: "direct" = a business using Storias on its own (one client, no PMs).
    agency_mode: Literal["multi", "direct"] = "multi"


class ClientUser(BaseModel):
    """Usuario de un cliente final — entra al panel /app con un link de acceso."""
    id: str
    client_id: str
    agency_id: str
    email: str
    name: str | None = None
    role: Literal["owner", "editor"]
