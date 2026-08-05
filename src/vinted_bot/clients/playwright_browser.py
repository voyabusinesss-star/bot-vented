"""Lancement Playwright — Chrome installé (moins bloqué que Chromium embarqué)."""

from __future__ import annotations

from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

# Réduit navigator.webdriver / traces automation visibles par DataDome.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def launch_vinted_browser(
    playwright: Any,
    *,
    headless: bool = True,
    proxy: dict[str, str] | None = None,
) -> Any:
    """Lance Chrome système pour Vinted.

    Préfère ``channel=chrome`` (Google Chrome installé) pour éviter le Chromium
    Playwright manquant / le ``chrome-headless-shell`` du cache sandbox.
    ``proxy`` : dict Playwright ``{server, username?, password?}``.
    """
    # --no-sandbox / shm : obligatoire dans Docker/Railway (sinon Chromium crash)
    common: dict[str, Any] = {
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
        ],
        "ignore_default_args": ["--enable-automation"],
    }
    if proxy:
        common["proxy"] = proxy
        log.info("playwright_proxy_enabled", server=proxy.get("server"))

    try:
        return playwright.chromium.launch(channel="chrome", headless=headless, **common)
    except Exception as chrome_exc:
        log.warning(
            "playwright_chrome_channel_failed",
            error=str(chrome_exc),
            headless=headless,
        )

    # Dernier recours : Chromium Playwright (nécessite `uv run playwright install chromium`)
    try:
        return playwright.chromium.launch(headless=headless, **common)
    except Exception as chromium_exc:
        raise RuntimeError(
            "Impossible de lancer Chrome/Chromium. "
            "Installe Google Chrome, ou lance : `uv run playwright install chromium`"
        ) from chromium_exc


def apply_vinted_stealth(context: Any) -> None:
    """Applique un léger masquage automation sur le contexte navigateur."""
    try:
        context.add_init_script(STEALTH_INIT_SCRIPT)
    except Exception as exc:
        log.debug("playwright_stealth_init_failed", error=str(exc))
