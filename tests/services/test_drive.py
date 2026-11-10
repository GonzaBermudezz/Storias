import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from app.services import drive


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color).save(buf, format="PNG")
    return buf.getvalue()


_ONE, _TWO, _FIRST = _png((255, 0, 0)), _png((0, 255, 0)), _png((0, 0, 255))


def _ignore_subfolders(files):
    """a79: _image_files also asks Drive for subfolders. These tests are about one
    flat folder, so the subfolder query answers empty and image queries keep the
    Mock's configured response."""
    images = files.list.return_value
    no_folders = Mock()
    no_folders.execute.return_value = {"files": []}
    files.list.side_effect = lambda **kwargs: no_folders if drive._FOLDER_MIME in kwargs["q"] else images
    return files


def test_drive_client_uses_service_account_when_no_refresh_token(monkeypatch):
    monkeypatch.setattr(drive, "_service_account_credentials", lambda: "service-account-creds")
    captured = {}
    monkeypatch.setattr(drive, "build", lambda *a, http, **k: captured.setdefault("http", http))
    drive._drive_client()
    assert captured["http"].credentials == "service-account-creds"


def test_drive_client_uses_oauth_credentials_when_refresh_token_given(monkeypatch):
    monkeypatch.setattr(drive, "_oauth_credentials", lambda token: f"oauth-creds-for-{token}")
    captured = {}
    monkeypatch.setattr(drive, "build", lambda *a, http, **k: captured.setdefault("http", http))
    drive._drive_client(refresh_token="rt-123")
    assert captured["http"].credentials == "oauth-creds-for-rt-123"


def test_shared_drive_paginates_downloads_and_preserves_order(monkeypatch):
    files = Mock()
    files.list.return_value.execute.side_effect = [
        {"files": [{"id": "one", "name": "one.jpg"}], "nextPageToken": "page-2"},
        {"files": [{"id": "two", "name": "two.png"}]},
    ]
    files.get_media.return_value.execute.side_effect = [_ONE, _TWO]
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))
    assert drive.list_images("folder") == [("one", "one.jpg", _ONE), ("two", "two.png", _TWO)]
    calls = [call for call in files.list.call_args_list if drive._FOLDER_MIME not in call.kwargs["q"]]
    assert len(calls) == 2
    assert calls[1].kwargs["pageToken"] == "page-2"
    for call in calls:
        assert call.kwargs["supportsAllDrives"] is True
        assert call.kwargs["includeItemsFromAllDrives"] is True
        assert "mimeType contains 'image/'" in call.kwargs["q"]
        assert "trashed = false" in call.kwargs["q"]
    assert files.get_media.call_args.kwargs == {"fileId": "two", "supportsAllDrives": True}


def test_empty_folder_returns_no_images(monkeypatch):
    files = Mock()
    files.list.return_value.execute.return_value = {"files": []}
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))
    assert drive.list_images("empty") == []
    files.get_media.assert_not_called()


def test_list_image_metadata_does_not_download_file_contents(monkeypatch):
    files = Mock()
    files.list.return_value.execute.return_value = {"files": [
        {"id": "two", "name": "two.png"}, {"id": "one", "name": "one.jpg"},
    ]}
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))

    assert drive.list_image_metadata("folder") == [("one", "one.jpg"), ("two", "two.png")]
    files.get_media.assert_not_called()


def test_download_images_stops_after_requested_readable_limit(monkeypatch):
    files = Mock()
    files.get_media.return_value.execute.side_effect = [b"not-an-image", _ONE, _TWO]
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))

    result = drive.download_images(
        [("bad", "bad.heic"), ("one", "one.jpg"), ("two", "two.png"),
         ("unused", "unused.jpg")],
        limit=2,
    )

    assert result == [("one", "one.jpg", _ONE), ("two", "two.png", _TWO)]
    assert files.get_media.call_count == 3


def test_missing_folder_fails_before_google(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(drive, "_drive_client", factory)
    with pytest.raises(ValueError, match="drive_folder_id"):
        drive.list_images("")
    factory.assert_not_called()


def test_first_image_downloads_only_first_sorted_file(monkeypatch):
    files = Mock()
    files.list.return_value.execute.return_value = {"files": [
        {"id": "z", "name": "z.jpg"}, {"id": "a", "name": "a.jpg"},
    ]}
    files.get_media.return_value.execute.return_value = _FIRST
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))

    assert drive.first_image("folder") == ("a", "a.jpg", _FIRST)
    files.get_media.assert_called_once_with(fileId="a", supportsAllDrives=True)


def test_unreadable_image_is_skipped_not_fatal(monkeypatch):
    files = Mock()
    files.list.return_value.execute.return_value = {"files": [
        {"id": "bad", "name": "corrupt.heic"}, {"id": "good", "name": "good.png"},
    ]}
    files.get_media.return_value.execute.side_effect = [b"not-a-real-image", _FIRST]
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))

    assert drive.list_images("folder") == [("good", "good.png", _FIRST)]


def test_first_image_skips_unreadable_files_until_one_decodes(monkeypatch):
    files = Mock()
    files.list.return_value.execute.return_value = {"files": [
        {"id": "bad", "name": "corrupt.heic"}, {"id": "good", "name": "good.png"},
    ]}
    files.get_media.return_value.execute.side_effect = [b"not-a-real-image", _FIRST]
    _ignore_subfolders(files)
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: files))

    assert drive.first_image("folder") == ("good", "good.png", _FIRST)


# ---------------------------------------------------------------------------
# a79: one level of subfolders
# ---------------------------------------------------------------------------
class _TreeFiles:
    """Fake files() that answers files.list by its q: images of a folder, or
    subfolders of a folder. ``broken`` folder ids raise when listed."""

    def __init__(self, images: dict[str, list[dict]], folders: dict[str, list[dict]], broken=()):
        self.images, self.folders, self.broken = images, folders, set(broken)
        self.queries: list[str] = []

    def list(self, q, **kwargs):
        self.queries.append(q)
        folder_id = q.split("'")[1]
        if folder_id in self.broken:
            raise RuntimeError(f"cannot read {folder_id}")
        found = self.folders.get(folder_id, []) if drive._FOLDER_MIME in q else self.images.get(folder_id, [])
        return SimpleNamespace(execute=lambda: {"files": found})


def _use_tree(monkeypatch, tree):
    monkeypatch.setattr(drive, "_drive_client", lambda *a, **k: SimpleNamespace(files=lambda: tree))


def test_lista_imagenes_de_subcarpetas_con_el_nombre_de_la_subcarpeta(monkeypatch):
    tree = _TreeFiles(
        images={"root": [{"id": "a", "name": "a.jpg"}], "osmo": [{"id": "x", "name": "x.jpg"}],
                "garmin": [{"id": "y", "name": "y.jpg"}]},
        folders={"root": [{"id": "osmo", "name": "Osmo Pocket"}, {"id": "garmin", "name": "Garmin"}]},
    )
    _use_tree(monkeypatch, tree)

    # Same (name, id) sort as always — case-sensitive, so "Garmin/…" sorts before "a.jpg".
    assert drive.list_image_metadata("root") == [
        ("y", "Garmin/y.jpg"), ("x", "Osmo Pocket/x.jpg"), ("a", "a.jpg"),
    ]
    assert drive.count_images("root") == 3


def test_solo_un_nivel_de_subcarpetas(monkeypatch):
    tree = _TreeFiles(
        images={"root": [], "sub": [{"id": "s", "name": "s.jpg"}], "deep": [{"id": "d", "name": "d.jpg"}]},
        folders={"root": [{"id": "sub", "name": "Sub"}], "sub": [{"id": "deep", "name": "Deep"}]},
    )
    _use_tree(monkeypatch, tree)

    assert drive.list_image_metadata("root") == [("s", "Sub/s.jpg")]
    assert not any("'deep'" in q for q in tree.queries)
    # Subfolders of a subfolder are never even asked for.
    assert not any("'sub'" in q and drive._FOLDER_MIME in q for q in tree.queries)


def test_subcarpeta_ilegible_no_rompe_el_resto(monkeypatch):
    tree = _TreeFiles(
        images={"root": [{"id": "a", "name": "a.jpg"}], "ok": [{"id": "o", "name": "o.jpg"}]},
        folders={"root": [{"id": "bad", "name": "Rota"}, {"id": "ok", "name": "Bien"}]},
        broken={"bad"},
    )
    _use_tree(monkeypatch, tree)

    assert drive.list_image_metadata("root") == [("o", "Bien/o.jpg"), ("a", "a.jpg")]


def test_carpeta_raiz_que_falla_sigue_propagando_el_error(monkeypatch):
    _use_tree(monkeypatch, _TreeFiles(images={}, folders={}, broken={"root"}))

    with pytest.raises(RuntimeError, match="cannot read root"):
        drive.list_image_metadata("root")


def test_subcarpetas_tienen_un_tope(monkeypatch):
    many = [{"id": f"f{i:03d}", "name": f"F{i:03d}"} for i in range(drive._MAX_SUBFOLDERS + 5)]
    tree = _TreeFiles(images={f["id"]: [{"id": f"i{f['id']}", "name": "p.jpg"}] for f in many},
                      folders={"root": many})
    _use_tree(monkeypatch, tree)

    assert drive.count_images("root") == drive._MAX_SUBFOLDERS
