import pytest
from fastapi import HTTPException

from app.services.errors import ServiceError


def test_service_error_is_an_http_exception_with_status_and_detail():
    error = ServiceError(409, "ocupado")
    assert isinstance(error, HTTPException)
    assert (error.status_code, error.detail) == (409, "ocupado")


def test_service_error_can_be_caught_as_http_exception():
    with pytest.raises(HTTPException):
        raise ServiceError(404, "nada")
