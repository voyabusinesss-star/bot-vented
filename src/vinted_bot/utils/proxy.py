"""Parsing des URLs proxy pour Playwright."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlparse


def _strip_wrapping_quotes(text: str) -> str:
    return text.strip().strip('"').strip("'")


def parse_proxy_url_list(raw: object) -> list[str]:
    """Accepte str CSV/newline, JSON array Railway, list, ou vide."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            out.extend(parse_proxy_url_list(item))
        return out
    text = _strip_wrapping_quotes(str(raw))
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parse_proxy_url_list(parsed)
        except json.JSONDecodeError:
            inner = text[1:-1].strip() if text.endswith("]") else text[1:]
            inner = _strip_wrapping_quotes(inner)
            if inner.startswith("http"):
                return [inner]
    parts = re.split(r"[\n,]+", text)
    out: list[str] = []
    for part in parts:
        cleaned = _strip_wrapping_quotes(part)
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = _strip_wrapping_quotes(cleaned[1:-1])
        if cleaned:
            out.append(cleaned)
    return out


def playwright_proxy_from_url(url: str) -> dict[str, str]:
    """
    Convertit ``http://user:pass@host:port`` en dict Playwright.

    Raises:
        ValueError: URL invalide / sans host.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("proxy URL vide")
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(f"proxy URL sans host: {url!r}")
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https", "socks5", "socks4"}:
        scheme = "http"
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    server = f"{scheme}://{parsed.hostname}:{port}"
    out: dict[str, str] = {"server": server}
    if parsed.username:
        out["username"] = unquote(parsed.username)
    if parsed.password:
        out["password"] = unquote(parsed.password)
    return out


def assign_proxy_for_worker(
    proxies: list[str],
    worker_id: int,
) -> str | None:
    """Proxy sticky : worker i → proxies[i % len]. None si liste vide."""
    if not proxies:
        return None
    return proxies[worker_id % len(proxies)]


def rotate_proxy(
    proxies: list[str],
    current: str | None,
) -> str | None:
    """Passe au proxy suivant dans la liste (rotation)."""
    if not proxies:
        return None
    if current is None or current not in proxies:
        return proxies[0]
    idx = proxies.index(current)
    return proxies[(idx + 1) % len(proxies)]
