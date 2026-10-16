from __future__ import annotations

from app.config import Settings


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        secret_key="test-secret",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_role_key="service-role",
        google_client_id="google-client",
        google_client_secret="google-secret",
        anthropic_api_key="anthropic",
        encryption_key="test-encryption-key",
        **kwargs,
    )


def test_trusted_hosts_defaults_include_railway_healthcheck(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)

    settings = _settings()

    assert settings.trusted_hosts == [
        "storias.app", "*.storias.app", "healthcheck.railway.app"
    ]


def test_trusted_hosts_custom_hosts_add_healthcheck_once_without_mutation():
    settings = _settings(allowed_hosts=["mi-app.up.railway.app"])

    assert settings.trusted_hosts == [
        "mi-app.up.railway.app", "healthcheck.railway.app"
    ]
    assert settings.allowed_hosts == ["mi-app.up.railway.app"]

    settings_with_healthcheck = _settings(
        allowed_hosts=["mi-app.up.railway.app", "healthcheck.railway.app"]
    )
    assert settings_with_healthcheck.trusted_hosts == [
        "mi-app.up.railway.app", "healthcheck.railway.app"
    ]
