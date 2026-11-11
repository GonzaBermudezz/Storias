from app.services import brand
from fake_db import FakeDB

CLIENT = {"id": "c1", "agency_id": "a1", "business_description": "viejo", "weekly_focus": None}


def test_apply_prompt_patch_without_changes_returns_current_and_skips_rpc():
    db = FakeDB([dict(CLIENT)])
    result = brand.apply_prompt_patch(db, "c1", "e1", "a1", {"business_description": "viejo"})
    assert result["id"] == "c1"
    assert db.ops("rpc") == []


def test_apply_prompt_patch_sends_only_changed_fields_to_the_rpc():
    db = FakeDB([dict(CLIENT), {"id": "c1", "business_description": "nuevo", "meta_access_token_encrypted": "s"}])
    result = brand.apply_prompt_patch(db, "c1", "e1", "a1", {"business_description": "nuevo", "weekly_focus": None})
    assert db.ops("rpc")[0][:3] == ("update_client_prompt", "rpc", {
        "p_client_id": "c1", "p_employee_id": "e1", "p_agency_id": "a1",
        "p_patch": {"business_description": "nuevo"}})
    assert result == {"id": "c1", "business_description": "nuevo"}


def test_apply_prompt_patch_accepts_a_list_and_falls_back_to_merging():
    db = FakeDB([dict(CLIENT), [{"id": "c1", "weekly_focus": "f"}]])
    assert brand.apply_prompt_patch(db, "c1", "e1", "a1", {"weekly_focus": "f"}) == {"id": "c1", "weekly_focus": "f"}
    db = FakeDB([dict(CLIENT), None])
    merged = brand.apply_prompt_patch(db, "c1", "e1", "a1", {"weekly_focus": "f"})
    assert merged["weekly_focus"] == "f" and merged["business_description"] == "viejo"


def test_set_weekly_format_updates_the_client_after_the_tenancy_check():
    db = FakeDB([dict(CLIENT), [{"id": "c1", "weekly_format": "teaser"}]])
    assert brand.set_weekly_format(db, "c1", "a1", "teaser") == {"id": "c1", "weekly_format": "teaser"}
    assert db.ops("update")[0][2] == {"weekly_format": "teaser"}


def test_set_weekly_format_falls_back_when_nothing_is_returned():
    db = FakeDB([dict(CLIENT), []])
    assert brand.set_weekly_format(db, "c1", "a1", None) == {"id": "c1", "weekly_format": None}
