from __future__ import annotations

from fastapi import HTTPException


class ServiceError(HTTPException):
    """Error raised by the portal services.

    It is an HTTPException so routers keep answering exactly what they did
    before the extraction, while non-HTTP callers (the CM loop) catch it and
    turn it into a tool_result error.
    """
