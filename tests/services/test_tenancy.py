import pytest

from app.services import tenancy
from app.services.errors import ServiceError
from fake_db import FakeDB


def test_client_or_error_returns_the_client_of_the_agency():
    db = FakeDB([{"id": "c1", "agency_id": "a1"}])
    assert tenancy.client_or_error(db, "c1", "a1")["id"] == "c1"


@pytest.mark.parametrize("response", [None, {}])
def test_client_or_error_404_when_missing(response):
    with pytest.raises(ServiceError) as info:
        tenancy.client_or_error(FakeDB([response]), "c1", "a1")
    assert (info.value.status_code, info.value.detail) == (404, "Cliente no encontrado")


def test_client_or_error_403_for_another_agency():
    with pytest.raises(ServiceError) as info:
        tenancy.client_or_error(FakeDB([{"id": "c1", "agency_id": "other"}]), "c1", "a1")
    assert (info.value.status_code, info.value.detail) == (403, "Cliente fuera de la agencia")


def test_without_secrets_drops_encrypted_columns():
    row = {"id": "c1", "drive_refresh_token_encrypted": "x", "meta_access_token_encrypted": "y"}
    assert tenancy.without_secrets(row) == {"id": "c1"}


def test_select_all_pages_until_a_short_page():
    full = [{"n": i} for i in range(tenancy.PAGE_SIZE)]
    db = FakeDB([full, [{"n": "last"}]])
    rows = tenancy.select_all(lambda: db.table("x").select("*"))
    assert len(rows) == tenancy.PAGE_SIZE + 1
    assert [call[3][-1][1] for call in db.calls] == [0, tenancy.PAGE_SIZE]
