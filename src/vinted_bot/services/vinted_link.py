"""Liaison compte Vinted ↔ membre Discord (session Playwright)."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, sync_playwright

from vinted_bot.config import get_settings
from vinted_bot.db.member_accounts import upsert_member_vinted_account
from vinted_bot.db.session import session_scope
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

VINTED_USERNAME_SELECTORS = (
    "[data-testid='profile-username']",
    "[data-testid='user-login']",
    ".user-login",
    "a[href*='/member/'] span",
)


def _extract_vinted_username(page: Page, base_url: str) -> str | None:
    """Tente de lire le pseudo Vinted après connexion."""
    try:
        page.goto(f"{base_url}/member/general", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    except Exception:
        return None

    for selector in VINTED_USERNAME_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=1500):
                text = loc.inner_text(timeout=2000).strip()
                if text and len(text) < 64:
                    return text
        except Exception:
            continue
    return None


BLOCK_MARKERS = ("session a été bloquée", "activité inhabituelle", "temporairement bloqué")


def _is_blocked_page(page: Page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return False
    return any(marker in text for marker in BLOCK_MARKERS)


def _launch_link_browser(playwright: Any) -> Any:
    """Chrome installé (moins détecté que Chromium embarqué)."""
    launch_args = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }
    try:
        return playwright.chromium.launch(channel="chrome", **launch_args)
    except Exception:
        log.warning("vinted_link_chrome_unavailable", fallback="chromium")
        return playwright.chromium.launch(**launch_args)


def link_vinted_account(
    *,
    discord_user_id: int,
    discord_username: str | None = None,
    announce_on_discord: bool = True,
) -> str | None:
    """Ouvre Vinted en navigateur ; l'utilisateur se connecte puis valide."""
    settings = get_settings()
    base_url = settings.vinted_base_url.rstrip("/")

    print(f"Liaison compte Vinted pour Discord user {discord_user_id}")
    print("1. Une fenêtre **Chrome** va s'ouvrir (pas Safari — c'est normal)")
    print(f"2. Connecte-toi sur Vinted ({base_url}) si besoin")
    print("3. Si page « session bloquée » → Entrée NE PAS, ferme et réessaie plus tard\n")

    vinted_username: str | None = None
    with sync_playwright() as playwright:
        browser = _launch_link_browser(playwright)
        context = browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        input(">>> Appuie sur Entrée UNIQUEMENT si tu vois Vinted normal (pas bloqué)... ")
        if _is_blocked_page(page):
            browser.close()
            raise RuntimeError(
                "Vinted affiche encore « session bloquée ». "
                "Attends quelques heures ou réessaie en 4G, puis relance vinted-link."
            )
        vinted_username = _extract_vinted_username(page, base_url)
        storage_state = context.storage_state()
        browser.close()

    with session_scope() as session:
        upsert_member_vinted_account(
            session,
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            vinted_username=vinted_username,
            storage_state=storage_state,
        )

    _ = announce_on_discord

    log.info(
        "vinted_account_linked",
        discord_user_id=discord_user_id,
        vinted_username=vinted_username,
    )
    label = vinted_username or "compte Vinted"
    print(f"OK — lié : Discord {discord_user_id} → Vinted **{label}**")
    return vinted_username
