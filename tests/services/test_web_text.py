import socket

import pytest

from app.services import web_text


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8", location=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        self._body = body
        self.encoding = "utf-8"

    def iter_content(self, chunk_size):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def close(self):
        pass


def _resolve_to(monkeypatch, mapping):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
    monkeypatch.setattr(web_text.socket, "getaddrinfo", fake_getaddrinfo)


def _serve(monkeypatch, responses, calls=None):
    queue = list(responses)

    def fake_get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        return queue.pop(0)
    monkeypatch.setattr(web_text.requests, "get", fake_get)


HTML = """<html><head><title>GBA Importadora</title>
<meta name="description" content="Garmin, DJI y Kindle con garantía oficial.">
<style>body{color:red}</style><script>var secret = 1;</script></head>
<body><nav>Inicio</nav><h1>Tecnología para moverte</h1><p>Somos   importadores
desde 2015.</p><noscript>Activá JS</noscript></body></html>"""


def test_reads_title_description_and_visible_text_only(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    calls = []
    _serve(monkeypatch, [FakeResponse(body=HTML.encode())], calls)

    text = web_text.fetch_text("gba.com.ar")

    assert text.startswith("GBA Importadora")
    assert "Garmin, DJI y Kindle con garantía oficial." in text
    assert "Tecnología para moverte" in text and "Somos importadores desde 2015." in text
    assert "secret" not in text and "color:red" not in text and "Activá JS" not in text
    url, kwargs = calls[0]
    assert url == "https://gba.com.ar"  # no scheme -> https
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True and kwargs["timeout"]


@pytest.mark.parametrize("url", ["ftp://gba.com.ar", "javascript:alert(1)", "http://", "https://user:pw@gba.com.ar"])
def test_rejects_bad_urls(monkeypatch, url):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1"])
def test_rejects_hosts_that_resolve_to_internal_addresses(monkeypatch, address):
    _resolve_to(monkeypatch, {"evil.test": address})
    _serve(monkeypatch, [])  # must never be called
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("http://evil.test")


def test_redirects_are_revalidated(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34", "internal.test": "10.1.2.3"})
    _serve(monkeypatch, [FakeResponse(status=302, location="http://internal.test/admin")])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")


def test_follows_a_safe_relative_redirect(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    calls = []
    _serve(monkeypatch, [FakeResponse(status=301, location="/inicio"), FakeResponse(body=b"<p>Hola</p>")], calls)
    assert web_text.fetch_text("https://gba.com.ar") == "Hola"
    assert calls[1][0] == "https://gba.com.ar/inicio"


def test_too_many_redirects(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [FakeResponse(status=302, location="/a") for _ in range(5)])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")


def test_rejects_non_html_and_errors(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [FakeResponse(body=b"%PDF", content_type="application/pdf"), FakeResponse(status=404)])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")


def test_caps_the_download_size(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [FakeResponse(body=b"<p>" + b"a " * (web_text.MAX_BYTES) + b"</p>")])
    assert len(web_text.fetch_text("https://gba.com.ar")) <= web_text.MAX_BYTES


def test_network_errors_become_web_text_errors(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})

    def boom(url, **kwargs):
        raise web_text.requests.ConnectionError("down")
    monkeypatch.setattr(web_text.requests, "get", boom)
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")
    _resolve_to(monkeypatch, {})
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://nadie.test")


@pytest.mark.parametrize("url", ["http://gba.com.ar:99999", "http://[::1", "gba.com.ar:abc"])
def test_malformed_urls_are_web_text_errors_not_500(monkeypatch, url):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text(url)


def test_a_redirect_to_a_malformed_location_is_a_web_text_error(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [FakeResponse(status=302, location="http://[::1/admin")])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")


@pytest.mark.parametrize("address", ["64:ff9b::7f00:1", "::127.0.0.1", "64:ff9b::a00:5"])
def test_rejects_ipv6_that_embeds_an_internal_ipv4(monkeypatch, address):
    _resolve_to(monkeypatch, {"evil.test": address})
    _serve(monkeypatch, [])
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("http://evil.test")


def test_allows_nat64_of_a_public_ipv4(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "64:ff9b::808:808"})
    _serve(monkeypatch, [FakeResponse(body=b"<p>Hola</p>")])
    assert web_text.fetch_text("https://gba.com.ar") == "Hola"


def test_a_slow_drip_server_hits_the_deadline(monkeypatch):
    _resolve_to(monkeypatch, {"gba.com.ar": "93.184.216.34"})
    _serve(monkeypatch, [FakeResponse(body=b"<p>" + b"a" * 20_000 + b"</p>")])
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(web_text.time, "monotonic", lambda: next(clock))
    with pytest.raises(web_text.WebTextError):
        web_text.fetch_text("https://gba.com.ar")
