"""Capture token Vinted (access_token_web) via Chrome — page /token Resello."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import sync_playwright

from vinted_bot.config import get_settings
from vinted_bot.services.vinted_link import _is_blocked_page, _launch_link_browser
from vinted_bot.services.vinted_login import normalize_vinted_base_url, open_vinted_home_for_login
from vinted_bot.services.vinted_profile import _current_user_payload
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

POLL_SECONDS = 2.5
TIMEOUT_SECONDS = 15 * 60


@dataclass
class TokenCaptureSession:
    sid: str
    status: str = "waiting"  # waiting | ready | error | expired
    access_token: str | None = None
    storage_state: dict[str, Any] | None = None
    vinted_username: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)


_sessions: dict[str, TokenCaptureSession] = {}
_lock = threading.Lock()


def get_token_session(sid: str) -> TokenCaptureSession | None:
    with _lock:
        return _sessions.get(sid)


def _extract_access_token(storage_state: dict[str, Any]) -> str | None:
    for cookie in storage_state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        if str(cookie.get("name") or "") == "access_token_web":
            value = str(cookie.get("value") or "").strip()
            if value:
                return value
    return None


def _set_session(sid: str, **kwargs: Any) -> None:
    with _lock:
        session = _sessions.get(sid)
        if session is None:
            return
        for key, value in kwargs.items():
            setattr(session, key, value)


def _run_capture(sid: str) -> None:
    settings = get_settings()
    base = normalize_vinted_base_url(settings.vinted_base_url)
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = _launch_link_browser(playwright)
        context = browser.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        open_vinted_home_for_login(page, base)
        log.info("token_capture_browser_opened", sid=sid)

        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with _lock:
                if sid not in _sessions:
                    return
            try:
                if _is_blocked_page(page):
                    _set_session(
                        sid,
                        status="error",
                        error="Vinted a bloqué la page. Réessaie plus tard ou change de réseau.",
                    )
                    return
                user = _current_user_payload(page, base)
                if isinstance(user, dict) and (user.get("login") or user.get("username")):
                    storage_state = context.storage_state()
                    token = _extract_access_token(storage_state)
                    if not token:
                        _set_session(
                            sid,
                            status="error",
                            error="Connecté mais cookie access_token_web introuvable.",
                        )
                        return
                    login = str(user.get("login") or user.get("username") or "").strip()
                    _set_session(
                        sid,
                        status="ready",
                        access_token=token,
                        storage_state=storage_state,
                        vinted_username=login or None,
                    )
                    log.info("token_capture_ready", sid=sid, username=login)
                    return
            except Exception as exc:  # noqa: BLE001
                log.debug("token_capture_poll_error", sid=sid, error=str(exc)[:120])
            time.sleep(POLL_SECONDS)

        _set_session(sid, status="error", error="Délai dépassé — reconnecte-toi sur Vinted.")
    except Exception as exc:
        log.exception("token_capture_failed", sid=sid, error=str(exc)[:160])
        _set_session(sid, status="error", error=f"Erreur navigateur : {exc}")
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if playwright is not None:
                playwright.stop()
        except Exception:
            pass


def start_token_capture() -> str:
    """Démarre une capture Chrome manuelle ; retourne l'id de session."""
    sid = secrets.token_urlsafe(16)
    with _lock:
        _sessions[sid] = TokenCaptureSession(sid=sid)
    thread = threading.Thread(
        target=_run_capture,
        args=(sid,),
        daemon=True,
        name=f"token-capture-{sid[:8]}",
    )
    thread.start()
    return sid


def capture_token_with_credentials(*, login: str, password: str) -> TokenCaptureSession:
    """Login email/mdp sur la page /token → session ready avec access_token_web."""
    from vinted_bot.services.vinted_login import login_vinted_with_credentials

    sid = secrets.token_urlsafe(16)
    session = TokenCaptureSession(sid=sid, status="waiting")
    with _lock:
        _sessions[sid] = session

    settings = get_settings()
    # Pas de log du mot de passe
    log.info("token_capture_credentials_start", login=login[:3] + "***")
    result = login_vinted_with_credentials(
        login=login,
        password=password,
        base_url=settings.vinted_base_url,
        # Visible : le headless donne souvent un faux cookie sans vrai login
        headless=False,
    )
    if not result.success or not result.storage_state:
        _set_session(
            sid,
            status="error",
            error=result.message or "Connexion Vinted échouée.",
        )
        return get_token_session(sid) or session

    token = _extract_access_token(result.storage_state)
    if not token:
        _set_session(
            sid,
            status="error",
            error="Connecté mais cookie access_token_web introuvable.",
        )
        return get_token_session(sid) or session

    _set_session(
        sid,
        status="ready",
        access_token=token,
        storage_state=result.storage_state,
        vinted_username=result.vinted_username,
    )
    log.info("token_capture_credentials_ready", username=result.vinted_username)
    return get_token_session(sid) or session


def resolve_token_capture_storage(raw: str) -> dict[str, Any] | None:
    """Résout un code sid OU le access_token_web affiché → storage_state complet."""
    code = (raw or "").strip()
    if not code or code.startswith("{"):
        return None
    if " " in code or "\n" in code:
        return None

    session = get_token_session(code)
    if session is not None and session.status == "ready" and session.storage_state:
        return dict(session.storage_state)

    # L'utilisateur a souvent collé le token brut : retrouver la session fraîche
    with _lock:
        candidates = [
            s
            for s in _sessions.values()
            if s.status == "ready"
            and s.storage_state
            and s.access_token
            and s.access_token == code
        ]
    if not candidates:
        return None
    best = max(candidates, key=lambda s: s.created_at)
    return dict(best.storage_state)
