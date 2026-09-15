from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import drive


def test_shared_drive_paginates_downloads_and_preserves_order(monkeypatch):
    files = Mock()
    files.list.return_value.execute.side_effect = [
        {"files": [{"id": "one", "name": "one.jpg"}], "nextPageToken": "page-2"},
        {"files": [{"id": "two", "name": "two.png"}]},
    ]
    files.get_media.return_value.execute.side_effect = [b"one", b"two"]
    monkeypatch.setattr(drive, "_drive_client", lambda: SimpleNamespace(files=lambda: files))
    assert drive.list_images("folder") == [("one", "one.jpg", b"one"), ("two", "two.png", b"two")]
    calls = files.list.call_args_list
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
    monkeypatch.setattr(drive, "_drive_client", lambda: SimpleNamespace(files=lambda: files))
    assert drive.list_images("empty") == []
    files.get_media.assert_not_called()


def test_missing_folder_fails_before_google(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(drive, "_drive_client", factory)
    with pytest.raises(ValueError, match="drive_folder_id"):
        drive.list_images("")
    factory.assert_not_called()
