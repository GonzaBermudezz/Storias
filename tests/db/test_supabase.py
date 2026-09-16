import asyncio
from types import SimpleNamespace

import pytest

from app.db import supabase


@pytest.fixture(autouse=True)
def clear_supabase_client_caches():
    supabase.close_supabase_clients()
    yield
    supabase.close_supabase_clients()


@pytest.mark.parametrize(
    ("factory_name", "expected_key"),
    [
        ("get_anon_client", "anon-key"),
        ("get_admin_client", "service-role-key"),
    ],
)
def test_supabase_clients_disable_http2_and_remain_cached(
    monkeypatch, factory_name, expected_key
):
    http_clients = []
    create_calls = []

    class FakeHttpClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            http_clients.append(self)

        def close(self):
            self.closed = True

    sentinel = object()

    def fake_create_client(url, key, *, options):
        create_calls.append((url, key, options))
        return sentinel

    settings = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-role-key",
    )
    monkeypatch.setattr(supabase, "get_settings", lambda: settings)
    monkeypatch.setattr(supabase.httpx, "Client", FakeHttpClient)
    monkeypatch.setattr(supabase, "create_client", fake_create_client)

    factory = getattr(supabase, factory_name)
    assert factory() is sentinel
    assert factory() is sentinel

    assert len(http_clients) == 1
    assert http_clients[0].kwargs["http2"] is False
    timeout = http_clients[0].kwargs["timeout"]
    assert timeout.connect == 10.0
    assert timeout.read == 120.0
    assert timeout.write == 120.0
    assert timeout.pool == 120.0
    assert len(create_calls) == 1
    url, key, options = create_calls[0]
    assert url == "https://example.supabase.co"
    assert key == expected_key
    assert options.httpx_client is http_clients[0]

    supabase.close_supabase_clients()

    assert http_clients[0].closed is True
    assert factory.cache_info().currsize == 0


def test_failed_supabase_construction_closes_transport(monkeypatch):
    class FakeHttpClient:
        closed = False

        def __init__(self, **kwargs):
            pass

        def close(self):
            self.closed = True

    http_client = FakeHttpClient()
    monkeypatch.setattr(supabase.httpx, "Client", lambda **kwargs: http_client)

    def fail_create_client(url, key, *, options):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(supabase, "create_client", fail_create_client)

    with pytest.raises(RuntimeError, match="construction failed"):
        supabase._create_client("https://example.supabase.co", "key")

    assert http_client.closed is True


def test_app_lifespan_closes_supabase_clients(monkeypatch):
    from app import main

    close_calls = []
    monkeypatch.setattr(main, "close_supabase_clients", lambda: close_calls.append(True))

    async def run_lifespan():
        async with main.lifespan(main.app):
            assert close_calls == []

    asyncio.run(run_lifespan())

    assert close_calls == [True]
