"""Read the visible text of a business website for the AI onboarding (fase 5).

The URL is typed by a portal user, so this is an SSRF surface: only http/https,
no credentials in the URL, every host (including each redirect target) must
resolve to public addresses only, and the download is capped in time and size.

Residual risk: the host is resolved here and again by ``requests`` when it
connects, so a DNS-rebinding host could still flip to an internal address in
between. The response is only turned into plain text for a prompt and never
returned raw, which limits what such an attacker could read.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 8
MAX_TEXT_CHARS = 20_000
_USER_AGENT = "StoriasBot/1.0 (+brand onboarding)"
_SKIPPED_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
_ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class WebTextError(Exception):
    """The site could not be read; the message is safe to show to the user."""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.description = ""
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            values = {key.lower(): (value or "") for key, value in attrs}
            if values.get("name", "").lower() in ("description", "og:description") or \
                    values.get("property", "").lower() == "og:description":
                self.description = self.description or values.get("content", "")
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self._skip_depth:
            self.parts.append(data)


def _clean(text: str) -> str:
    return " ".join(text.split())


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def _is_public(address) -> bool:
    """Public unicast only. IPv6 that embeds an IPv4 (NAT64, IPv4-compatible,
    IPv4-mapped) is judged by the embedded IPv4, which ``is_global`` misses."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif address in _NAT64 or address in _IPV4_COMPATIBLE:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address.is_global and not address.is_multicast


def _check_url(url: str) -> str:
    invalid = "La dirección de la web no es válida. Probá con algo como https://tunegocio.com"
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:  # bad port or bracketed IPv6
        raise WebTextError(invalid) from exc
    if parsed.scheme not in ("http", "https") or not hostname:
        raise WebTextError(invalid)
    if parsed.username or parsed.password:
        raise WebTextError("La dirección de la web no puede tener usuario ni contraseña.")
    port = port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise WebTextError("No encontré esa web. Revisá que la dirección esté bien escrita.") from exc
    for info in infos:
        if not _is_public(ipaddress.ip_address(info[4][0].split("%", 1)[0])):
            raise WebTextError("Esa dirección no es una web pública.")
    return url


def _normalize(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url and not url.lower().startswith(("javascript:", "data:", "mailto:")):
        url = f"https://{url}"
    return url


def _download(url: str) -> tuple[bytes, str, str]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        try:
            response = requests.get(
                url, timeout=(4, TIMEOUT_SECONDS), allow_redirects=False, stream=True,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
            )
        except requests.RequestException as exc:
            raise WebTextError("No pude abrir esa web. Probá de nuevo o contá el negocio en tus palabras.") from exc
        try:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                try:
                    url = urljoin(url, response.headers["Location"])
                except ValueError as exc:
                    raise WebTextError("La web redirige a una dirección inválida.") from exc
                continue
            if response.status_code != 200:
                raise WebTextError(f"La web respondió con un error ({response.status_code}).")
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type and content_type not in _ALLOWED_TYPES:
                raise WebTextError("Esa dirección no es una página web (parece un archivo).")
            body = bytearray()
            # Small chunks so the deadline is checked often: a server that
            # drips bytes can't hold the worker past TIMEOUT_SECONDS for long.
            for chunk in response.iter_content(1024):
                body.extend(chunk)
                if len(body) >= MAX_BYTES:
                    del body[MAX_BYTES:]
                    break
                if time.monotonic() > deadline:
                    raise WebTextError("La web tardó demasiado en responder.")
            return bytes(body), content_type, response.encoding or "utf-8"
        finally:
            response.close()
    raise WebTextError("La web redirige demasiadas veces.")


def fetch_text(url: str) -> str:
    """Title, meta description and visible text of the page, whitespace-collapsed.
    Raises WebTextError with a user-facing message on any problem."""
    body, content_type, encoding = _download(_normalize(url))
    try:
        html = body.decode(encoding, errors="replace")
    except LookupError:
        html = body.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        return _clean(html)[:MAX_TEXT_CHARS]
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep whatever was parsed
        pass
    pieces = [_clean("".join(parser.title)), _clean(parser.description), _clean(" ".join(parser.parts))]
    return " ".join(piece for piece in pieces if piece)[:MAX_TEXT_CHARS]
