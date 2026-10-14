from __future__ import annotations

from types import SimpleNamespace

import sentry_sdk

from app import observability


def test_init_sentry_is_disabled_without_dsn(monkeypatch):
    called = False

    def unexpected_init(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(sentry_sdk, "init", unexpected_init)

    assert observability.init_sentry(settings=SimpleNamespace(
        sentry_dsn="", environment="development"
    )) is False
    assert called is False


def test_init_sentry_uses_safe_defaults(monkeypatch):
    calls = []
    tags = []

    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: False)
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *args: tags.append(args))

    result = observability.init_sentry(
        "worker",
        settings=SimpleNamespace(
            sentry_dsn="https://key@o0.ingest.sentry.io/1", environment="production"
        ),
    )

    assert result is True
    assert len(calls) == 1
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["include_local_variables"] is False
    assert calls[0]["max_request_body_size"] == "never"
    assert calls[0]["before_send"] is observability.before_send
    assert tags == [("service", "worker")]


def test_init_sentry_does_not_reinitialize(monkeypatch):
    called = False

    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: True)

    def unexpected_init(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(sentry_sdk, "init", unexpected_init)

    assert observability.init_sentry(settings=SimpleNamespace(
        sentry_dsn="https://key@o0.ingest.sentry.io/1", environment="production"
    )) is True
    assert called is False


def test_before_send_scrubs_nested_tokens_and_sensitive_keys():
    meta_token = "EAB" + "x" * 30
    bearer_token = "fake-bearer-token-value"
    jwt_token = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    event = {
        "url": "https://example.test/?access_token=ABC123&x=1",
        "nested": [
            "normal text",
            {
                "accessToken": "SECRET_DIRECT_VALUE",
                "headers": {"Authorization": f"Bearer {bearer_token}"},
                "meta_access_token": meta_token,
                "credentials": {
                    "secretKey": "SECRET_CAMELCASE_VALUE",
                    "clientSecretKey": "CLIENT_SECRET_CAMELCASE_VALUE",
                },
                "detail": f"jwt {jwt_token}",
            },
        ],
    }

    result = observability.before_send(event, None)

    assert "ABC123" not in str(result)
    assert "SECRET_DIRECT_VALUE" not in str(result)
    assert "SECRET_CAMELCASE_VALUE" not in str(result)
    assert "CLIENT_SECRET_CAMELCASE_VALUE" not in str(result)
    assert meta_token not in str(result)
    assert bearer_token not in str(result)
    assert jwt_token not in str(result)
    assert result["nested"][0] == "normal text"
    assert result["nested"][1]["accessToken"] == "[redacted]"
    assert result["nested"][1]["headers"]["Authorization"] == "[redacted]"
    assert result["nested"][1]["credentials"]["secretKey"] == "[redacted]"
    assert result["nested"][1]["credentials"]["clientSecretKey"] == "[redacted]"
    assert result["url"] == "https://example.test/?access_token=[redacted]&x=1"


def test_before_breadcrumb_drops_http_and_scrubs_other_categories():
    assert observability.before_breadcrumb({"category": "httpx"}, None) is None
    assert observability.before_breadcrumb({"category": "httplib"}, None) is None

    eab_token = "EAB" + "z" * 30
    bearer_token = "fake-breadcrumb-bearer"
    result = observability.before_breadcrumb(
        {
            "category": "task",
            "message": f"access_token=ABC123 Bearer {bearer_token} {eab_token}",
            "data": {"password": "SECRET_PASSWORD", "note": "safe breadcrumb"},
        },
        None,
    )

    assert "ABC123" not in str(result)
    assert bearer_token not in str(result)
    assert eab_token not in str(result)
    assert result["data"]["password"] == "[redacted]"
    assert result["data"]["note"] == "safe breadcrumb"
