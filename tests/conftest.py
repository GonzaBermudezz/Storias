import os

import pytest

# The developer's .env must not change test behavior: with CM_ENABLED=true there,
# /portal/me would query the real Supabase. Env vars win over .env in pydantic-settings,
# and this runs before any Settings() is built. CM tests turn it on explicitly.
os.environ["CM_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _no_real_photo_classification(monkeypatch):
    """a74: el clasificador de fotos nunca sale a internet en los tests."""
    from app.services import image_tags
    monkeypatch.setattr(image_tags, "classify_image", lambda image_bytes: None)
