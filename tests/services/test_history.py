import pytest

from app.services import history
from app.services.errors import ServiceError
from fake_db import FakeDB


def test_prompt_history_flattens_the_employee_name():
    rows = [{"id": "h1", "employees": {"name": "Ana"}}, {"id": "h2", "employees": None}]
    result = history.prompt_history(FakeDB([{"id": "c1", "agency_id": "a1"}, rows]), "c1", "a1")
    assert [(r["id"], r["changed_by_name"]) for r in result] == [("h1", "Ana"), ("h2", None)]
    assert all("employees" not in r for r in result)


def test_prompt_history_checks_the_agency():
    with pytest.raises(ServiceError) as info:
        history.prompt_history(FakeDB([{"id": "c1", "agency_id": "other"}]), "c1", "a1")
    assert info.value.status_code == 403


def test_prompt_history_empty():
    assert history.prompt_history(FakeDB([{"id": "c1", "agency_id": "a1"}, None]), "c1", "a1") == []
