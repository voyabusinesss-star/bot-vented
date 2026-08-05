"""Parsing des URLs proxy pour Playwright."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse


def parse_proxy_url_list(raw: object) -> list[str]:
    """Accepte str CSV/newline, list, ou vide → liste d'URLs non vides."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    parts = re.split(r"[\n,]+", text)
    return [p.strip() for p in parts if p.strip()]


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
