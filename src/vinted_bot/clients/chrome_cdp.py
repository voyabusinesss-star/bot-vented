"""Chrome réel via CDP — hors `playwright.launch` (moins flagué DataDome)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_PROFILE_DIR = ".data/chrome-cdp"

# Dernier mode lancé — évite de relancer Chrome inutilement.
_last_cdp_headless: bool | None = None


def resolve_chrome_binary() -> str | None:
    """Chemin Google Chrome (macOS / Linux / PATH)."""
    candidates = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    )
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def is_cdp_ready(port: int, *, timeout_s: float = 1.5) -> bool:
    url = f"{cdp_endpoint(port)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return bool(data.get("webSocketDebuggerUrl") or data.get("Browser"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def ensure_chrome_cdp(
    *,
    port: int = DEFAULT_CDP_PORT,
    user_data_dir: str | Path = DEFAULT_PROFILE_DIR,
    cdp_url: str = "",
    headless: bool = True,
    force_restart: bool = False,
) -> str:
    """Retourne l'URL CDP ; démarre Chrome si besoin (profil dédié).

    ``headless=True`` (défaut) : pas de fenêtre visible.
    Réutilise le Chrome déjà lancé si le mode (headless/visible) est identique.
    """
    global _last_cdp_headless

    explicit = (cdp_url or "").strip()
    if explicit:
        if explicit.rstrip("/").endswith(str(port)) or "9222" in explicit:
            if is_cdp_ready(port):
                return explicit.rstrip("/")
        return explicit.rstrip("/")

    same_mode = _last_cdp_headless is headless
    if is_cdp_ready(port) and same_mode and not force_restart:
        log.info("chrome_cdp_reused", port=port, headless=headless)
        return cdp_endpoint(port)

    if is_cdp_ready(port):
        stop_chrome_cdp(port)

    binary = resolve_chrome_binary()
    if not binary:
        raise RuntimeError(
            "Google Chrome introuvable. Installe Chrome, ou lance-le avec "
            f"--remote-debugging-port={port}."
        )

    profile = Path(user_data_dir).expanduser()
    if not profile.is_absolute():
        profile = Path.cwd() / profile
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        binary,
        f"--remote-debugging-port={int(port)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--lang=fr-FR",
        "--accept-lang=fr-FR,fr,en-US,en",
    ]
    if headless:
        # headless=new = vrai Chrome sans UI (moins trivial à détecter que l'ancien headless)
        cmd.extend(
            [
                "--headless=new",
                "--window-size=1440,900",
            ]
        )
    else:
        # Fenêtre visible — utile si captcha humain.
        cmd.extend(["--window-size=1440,900"])
    cmd.append("about:blank")
    log.info("chrome_cdp_starting", port=port, profile=str(profile), headless=headless)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if is_cdp_ready(port):
            _last_cdp_headless = headless
            log.info("chrome_cdp_ready", port=port, headless=headless)
            return cdp_endpoint(port)
        time.sleep(0.25)

    raise RuntimeError(
        f"Chrome CDP n'a pas démarré sur le port {port}. "
        "Ferme les autres instances Chrome CDP, ou augmente le délai."
    )


def sanitize_storage_state_for_buy(storage_state: dict) -> dict:
    """Retire les cookies DataDome (souvent déjà en hard-block)."""
    cookies = storage_state.get("cookies")
    if not isinstance(cookies, list):
        return storage_state
    cleaned = [
        c
        for c in cookies
        if isinstance(c, dict)
        and "datadome" not in str(c.get("name", "")).lower()
        and "datadome" not in str(c.get("domain", "")).lower()
    ]
    out = dict(storage_state)
    out["cookies"] = cleaned
    return out


def _normalize_cookie_for_playwright(cookie: dict[str, Any]) -> dict[str, Any] | None:
    name = cookie.get("name")
    if not name:
        return None
    out: dict[str, Any] = {
        "name": str(name),
        "value": str(cookie.get("value", "")),
    }
    domain = cookie.get("domain")
    path = cookie.get("path") or "/"
    url = cookie.get("url")
    if url:
        out["url"] = str(url)
    elif domain:
        out["domain"] = str(domain)
        out["path"] = str(path)
    else:
        return None
    for key in ("expires", "httpOnly", "secure"):
        if key in cookie:
            out[key] = cookie[key]
    same_site = cookie.get("sameSite")
    if isinstance(same_site, str):
        normalized = same_site.strip().lower()
        if normalized == "strict":
            out["sameSite"] = "Strict"
        elif normalized == "lax":
            out["sameSite"] = "Lax"
        elif normalized == "none":
            out["sameSite"] = "None"
    return out


def apply_storage_state_to_context(context: Any, storage_state: dict) -> int:
    """Injecte les cookies dans le contexte CDP par défaut (pas de new_context)."""
    raw = storage_state.get("cookies") or []
    cookies: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                normalized = _normalize_cookie_for_playwright(item)
                if normalized is not None:
                    cookies.append(normalized)
    try:
        context.clear_cookies()
    except Exception as exc:
        log.debug("chrome_cdp_clear_cookies_failed", error=str(exc))
    if cookies:
        context.add_cookies(cookies)
    return len(cookies)


def stop_chrome_cdp(port: int = DEFAULT_CDP_PORT) -> None:
    """Tue le Chrome qui écoute le port CDP."""
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f"tcp:{int(port)}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    pids = {int(p) for p in out.split() if p.strip().isdigit()}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            log.info("chrome_cdp_stopped", pid=pid, port=port)
        except OSError:
            continue
    time.sleep(0.6)


def ensure_fresh_chrome_cdp(
    *,
    port: int = DEFAULT_CDP_PORT,
    user_data_dir: str | Path = DEFAULT_PROFILE_DIR,
    cdp_url: str = "",
    restart: bool = False,
    headless: bool = True,
) -> str:
    """CDP prêt ; ``restart=True`` force un nouveau process (changement headless↔visible)."""
    return ensure_chrome_cdp(
        port=port,
        user_data_dir=user_data_dir,
        cdp_url=cdp_url,
        headless=headless,
        force_restart=restart,
    )
