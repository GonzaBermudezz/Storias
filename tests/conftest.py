import pytest


@pytest.fixture(autouse=True)
def _no_real_photo_classification(monkeypatch):
    """a74: el clasificador de fotos nunca sale a internet en los tests."""
    from app.services import image_tags
    monkeypatch.setattr(image_tags, "classify_image", lambda image_bytes: None)
