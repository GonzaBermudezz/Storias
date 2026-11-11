import pytest

from app.services import content_jobs, photos
from app.services.errors import ServiceError
from fake_db import FakeDB

CLIENT = {"id": "c1", "agency_id": "a1", "drive_folder_id": "f1"}


def test_photos_summary_without_folder():
    out = photos.photos_summary(FakeDB([]), {"id": "c1", "drive_folder_id": None}, None)
    assert out == {"carpeta": False, "fotos": [], "semanas_sin_repetir": content_jobs.NO_REPEAT_WEEKS}


def test_photos_summary_builds_state_and_tags(monkeypatch):
    monkeypatch.setattr(photos.drive, "list_image_metadata", lambda folder, token: [("p1", "Sub/uno.jpg"), ("p2", "dos.jpg")])
    monkeypatch.setattr(photos.content_jobs, "_load_tags", lambda db, cid: {"p1": {"producto": "taza", "escena": "producto", "persona": True}})
    seen = []
    db = FakeDB([[{"drive_file_id": "p1", "last_used_at": None, "times_used": 2}]])
    out = photos.photos_summary(db, dict(CLIENT), "tok", on_listed=seen.append)
    assert seen == [["p1", "p2"]]
    first, second = out["fotos"]
    assert (first["nombre"], first["carpeta"], first["producto"], first["escena"], first["persona"]) == ("uno.jpg", "Sub", "taza", "Producto", True)
    assert (first["veces_usada"], first["estado"]) == (2, "nueva")
    assert (second["analizada"], second["estado"], second["carpeta"]) == (False, "nueva", None)


def test_photos_summary_drive_failure_is_422(monkeypatch):
    def boom(folder, token):
        raise RuntimeError("x")

    monkeypatch.setattr(photos.drive, "list_image_metadata", boom)
    with pytest.raises(ServiceError) as info:
        photos.photos_summary(FakeDB([]), dict(CLIENT), None)
    assert info.value.status_code == 422
