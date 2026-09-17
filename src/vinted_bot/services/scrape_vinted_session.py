"""Session Vinted dédiée au scrape (compte bot, séparé des membres autobuy).

La session est créée / rafraîchie sur **IP Railway** (Playwright sur bot-scrape).
Elle n'utilise pas l'IP du Mac ni les comptes membres Discord.
"""

from __future__ import annotations

import json
from typing import Any

from vinted_bot.db.repositories import get_checkpoint, set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.services.vinted_oauth import (
    exchange_refresh_token,
    storage_state_with_tokens,
)
from vinted_bot.services.vinted_token import (
    VintedTokenError,
    parse_vinted_token_to_storage_state,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_CHECKPOINT_KEY = "scrape_ops:vinted_session"


def _cookie_names(state: dict[str, Any]) -> set[str]:
    return {
        str(c.get("name") or "")
        for c in (state.get("cookies") or [])
        if isinstance(c, dict) and c.get("name")
    }


def hydrate_scrape_storage_state(
    state: dict[str, Any],
    *,
    settings: Any | None = None,
) -> dict[str, Any] | None:
    """Si seul refresh_token_web est présent, obtient access_token_web via OAuth."""
    from vinted_bot.config import get_settings

    cfg = settings or get_settings()
    names = _cookie_names(state)
    if "access_token_web" in names:
        return state

    refresh_value = ""
    for cookie in state.get("cookies") or []:
        if isinstance(cookie, dict) and cookie.get("name") == "refresh_token_web":
            refresh_value = str(cookie.get("value") or "").strip()
            break
    if not refresh_value:
        return state

    try:
        tokens = exchange_refresh_token(
            refresh_value,
            base_url=str(cfg.vinted_base_url or "https://www.vinted.fr"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("scrape_session_oauth_refresh_failed", error=str(exc)[:200])
        return state

    hydrated = storage_state_with_tokens(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token") or refresh_value,
    )
    save_scrape_storage_state(hydrated, source="oauth_refresh")
    log.info(
        "scrape_session_hydrated_from_refresh",
        has_access=True,
        has_refresh=True,
    )
    return hydrated


def scrape_session_configured(*, settings: Any | None = None) -> bool:
    from vinted_bot.config import get_settings

    cfg = settings or get_settings()
    if str(getattr(cfg, "vinted_scrape_session", "") or "").strip():
        return True
    try:
        with session_scope() as session:
            data = get_checkpoint(session, _CHECKPOINT_KEY)
        return bool(data and data.get("storage_state"))
    except Exception:  # noqa: BLE001
        return False


def load_scrape_storage_state(*, settings: Any | None = None) -> dict[str, Any] | None:
    """Charge storage_state Playwright (env VINTED_SCRAPE_SESSION puis Postgres)."""
    from vinted_bot.config import get_settings

    cfg = settings or get_settings()
    raw = str(getattr(cfg, "vinted_scrape_session", "") or "").strip()
    if raw:
        try:
            state = parse_vinted_token_to_storage_state(raw)
            return hydrate_scrape_storage_state(state, settings=cfg)
        except VintedTokenError as exc:
            log.warning("scrape_session_env_invalid", error=str(exc)[:160])
            return None

    try:
        with session_scope() as session:
            data = get_checkpoint(session, _CHECKPOINT_KEY)
        if not data:
            return None
        state = data.get("storage_state")
        if isinstance(state, dict) and state.get("cookies"):
            return hydrate_scrape_storage_state(dict(state), settings=cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("scrape_session_checkpoint_load_failed", error=str(exc)[:160])
    return None


def save_scrape_storage_state(
    storage_state: dict[str, Any],
    *,
    vinted_username: str | None = None,
    source: str = "bootstrap",
) -> None:
    cookies = storage_state.get("cookies") or []
    with session_scope() as session:
        set_checkpoint(
            session,
            _CHECKPOINT_KEY,
            {
                "storage_state": storage_state,
                "vinted_username": vinted_username,
                "source": source,
                "cookie_count": len(cookies),
            },
        )
    log.info(
        "scrape_session_saved",
        source=source,
        vinted_username=vinted_username,
        cookie_count=len(cookies),
    )


def bootstrap_scrape_session_from_credentials(*, headless: bool = True) -> bool:
    """
    Connexion Vinted sur bot-scrape (IP Railway) avec identifiants dédiés.
    Nécessite VINTED_SCRAPE_LOGIN + VINTED_SCRAPE_PASSWORD en variables Railway.
    """
    from vinted_bot.config import get_settings
    from vinted_bot.services.vinted_login import login_vinted_with_credentials

    cfg = get_settings()
    login = str(getattr(cfg, "vinted_scrape_login", "") or "").strip()
    password = str(getattr(cfg, "vinted_scrape_password", "") or "").strip()
    if not login or not password:
        log.warning(
            "scrape_session_bootstrap_skipped",
            reason="missing_vinted_scrape_login_or_password",
        )
        return False

    log.info("scrape_session_bootstrap_start", login_prefix=login[:3] + "***")
    result = login_vinted_with_credentials(
        login=login,
        password=password,
        base_url=cfg.vinted_base_url,
        headless=headless,
    )
    if not result.success or not result.storage_state:
        log.warning(
            "scrape_session_bootstrap_failed",
            message=(result.message or "")[:200],
        )
        return False

    save_scrape_storage_state(
        result.storage_state,
        vinted_username=result.vinted_username,
        source="credentials_bootstrap",
    )
    log.info(
        "scrape_session_bootstrap_ok",
        vinted_username=result.vinted_username,
    )
    return True


def ensure_scrape_vinted_session(*, headless: bool = True) -> bool:
    """Charge ou crée la session scrape avant le pool permanent."""
    if load_scrape_storage_state() is not None:
        log.info("scrape_session_ready", source="env_or_checkpoint")
        return True
    if bootstrap_scrape_session_from_credentials(headless=headless):
        return True
    log.warning(
        "scrape_session_missing",
        hint=(
            "Créer un compte Vinted DÉDIÉ (pas ton compte perso), puis sur Railway bot-scrape: "
            "VINTED_SCRAPE_LOGIN + VINTED_SCRAPE_PASSWORD → redeploy. "
            "La connexion se fera sur IP Railway, pas chez toi."
        ),
    )
    return False


def export_scrape_session_code() -> str | None:
    """Code court à coller dans VINTED_SCRAPE_SESSION (sans cookies en clair dans les logs)."""
    state = load_scrape_storage_state()
    if not state:
        return None
    return json.dumps(state, separators=(",", ":"))
