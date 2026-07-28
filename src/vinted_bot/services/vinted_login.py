"""Connexion Vinted via Playwright (liaison self-service)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from vinted_bot.clients.playwright_browser import (
    apply_vinted_stealth,
    launch_vinted_browser,
)
from vinted_bot.services.vinted_link import (
    BLOCK_MARKERS,
    _extract_vinted_username,
    _is_blocked_page,
)
from vinted_bot.services.vinted_profile import _current_user_payload
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

VINTED_HOME_URL = "https://www.vinted.fr/"
# Écran auth (inscription / connexion) — évite de dépendre du bouton header
VINTED_AUTH_SELECT_URL = "https://www.vinted.fr/member/signup/select_type?ref_url=%2F"


def normalize_vinted_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base in {"https://vinted.fr", "http://vinted.fr"}:
        return "https://www.vinted.fr"
    return base


def vinted_login_entry_url(base_url: str) -> str:
    """Page d'accueil Vinted — point d'entrée liaison."""
    return VINTED_HOME_URL


def _fix_vinted_www_url(url: str) -> str | None:
    """vinted.fr sans www → www.vinted.fr (évite la 404 sur /users/login)."""
    parsed = urlparse(url)
    if parsed.netloc == "vinted.fr":
        return url.replace("://vinted.fr", "://www.vinted.fr", 1)
    return None


def attach_vinted_navigation_guard(page: Page) -> None:
    """Corrige uniquement vinted.fr → www.vinted.fr. Ne bloque pas la connexion."""

    page.add_init_script(
        """
        () => {
            const fixLinks = () => {
                document.querySelectorAll('a[href*="vinted.fr"]').forEach((node) => {
                    const href = node.getAttribute('href');
                    if (href && href.includes('://vinted.fr')) {
                        node.setAttribute('href', href.replace('://vinted.fr', '://www.vinted.fr'));
                    }
                });
            };
            fixLinks();
            new MutationObserver(fixLinks).observe(document.documentElement, {
                childList: true,
                subtree: true,
            });
        }
        """
    )

    def on_nav(frame) -> None:
        if frame != page.main_frame:
            return
        fixed = _fix_vinted_www_url(frame.url)
        if fixed and fixed != frame.url:
            try:
                page.goto(fixed, wait_until="domcontentloaded", timeout=10_000)
            except Exception:
                pass

    page.on("framenavigated", on_nav)


def _accept_cookies(page: Page) -> None:
    for selector in (
        "#onetrust-accept-btn-handler",
        "button[data-testid='cookie-policy-dialog-accept']",
        "button:has-text('Tout accepter')",
        "button:has-text('Accept all')",
        "#accept-recommended-btn-handler",
        "button.onetrust-close-btn-handler",
    ):
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1200):
                btn.click(timeout=3000, force=True)
                page.wait_for_timeout(400)
                break
        except Exception:
            continue
    # Le filtre sombre OneTrust peut rester et bloquer tous les clics
    try:
        page.evaluate(
            """() => {
                const sdk = document.getElementById('onetrust-consent-sdk');
                if (sdk) sdk.remove();
                document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-banner-sdk, #onetrust-pc-sdk')
                  .forEach((el) => el.remove());
                document.body && document.body.classList.remove('ot-overflow-y-hidden');
                document.documentElement && document.documentElement.classList.remove('ot-overflow-y-hidden');
            }"""
        )
    except Exception:
        pass


def _set_input_value(page: Page, selectors: tuple[str, ...], value: str) -> bool:
    """Remplit un input même si un overlay intercepte les clics Playwright."""
    for selector in selectors:
        try:
            field = page.locator(selector).first
            if field.count() == 0:
                continue
            if not field.is_visible(timeout=2000):
                continue
            try:
                field.fill(value, timeout=3000, force=True)
                return True
            except Exception:
                ok = page.evaluate(
                    """({sel, val}) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        const desc = Object.getOwnPropertyDescriptor(
                          window.HTMLInputElement.prototype, 'value'
                        );
                        if (desc && desc.set) {
                          desc.set.call(el, val);
                        } else {
                          el.value = val;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }""",
                    {"sel": selector, "val": value},
                )
                if ok:
                    return True
        except Exception:
            continue
    return False


def _click_first_visible(page: Page, selectors: tuple[str, ...], *, timeout_ms: int = 2000) -> bool:
    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=timeout_ms):
                btn.click(timeout=5000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


def _click_testid(page: Page, testid: str, *, timeout_ms: int = 8000) -> bool:
    """Clic fiable sur un data-testid Vinted (souvent plus stable via DOM click)."""
    selector = f"[data-testid='{testid}']"
    try:
        page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    except Exception:
        return False
    try:
        clicked = page.evaluate(
            """(tid) => {
                const el = document.querySelector(`[data-testid="${tid}"]`);
                if (!el) return false;
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                el.click();
                return true;
            }""",
            testid,
        )
        if clicked:
            page.wait_for_timeout(900)
            return True
    except Exception:
        pass
    try:
        loc = page.locator(selector).first
        loc.click(timeout=5000, force=True)
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def open_vinted_home_for_login(page: Page, base_url: str = "") -> None:
    attach_vinted_navigation_guard(page)
    page.goto(VINTED_HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    _accept_cookies(page)


def _username_field_visible(page: Page) -> bool:
    try:
        return page.locator(
            "input[name='username'], input[name='login'], input[type='email'], "
            "input[autocomplete='username'], input[autocomplete='email']"
        ).first.is_visible(timeout=1500)
    except Exception:
        return False


def _open_auth_select_page(page: Page) -> None:
    """Ouvre l'écran Apple/Google/Facebook + e-mail (sans dépendre du header)."""
    attach_vinted_navigation_guard(page)
    page.goto(VINTED_AUTH_SELECT_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _accept_cookies(page)
    if _is_blocked_page(page):
        raise RuntimeError(
            "Vinted a bloqué l'accès (anti-bot). Réessaie plus tard ou utilise la connexion manuelle."
        )
    for testid in (
        "select-type-register-view",
        "select-type-login-view",
        "auth-select-type--register-switch",
        "auth-select-type--login-email",
        "auth-select-type--register-email",
        "google-oauth-button",
    ):
        try:
            if page.locator(f"[data-testid='{testid}']").count() > 0:
                return
        except Exception:
            continue
    raise RuntimeError(
        "Page de connexion Vinted inaccessible. Réessaie ou utilise la connexion manuelle."
    )


def open_vinted_login_page(page: Page, base_url: str) -> None:
    """Parcours Vinted actuel : select_type → Se connecter → e-mail."""
    _open_auth_select_page(page)

    on_login = page.locator("[data-testid='select-type-login-view']").count() > 0
    if not on_login:
        if not _click_testid(page, "auth-select-type--register-switch"):
            raise RuntimeError("Impossible de basculer vers Se connecter sur Vinted.")
        # Attendre la vue login
        for _ in range(10):
            if page.locator("[data-testid='select-type-login-view']").count() > 0:
                break
            page.wait_for_timeout(300)

    if not _click_testid(page, "auth-select-type--login-email"):
        if not _click_testid(page, "auth-select-type--register-email"):
            if not _username_field_visible(page):
                raise RuntimeError("Option connexion par e-mail Vinted introuvable.")

    for _ in range(10):
        if _username_field_visible(page):
            return
        page.wait_for_timeout(400)
    raise RuntimeError("Champ identifiant Vinted introuvable après ouverture e-mail.")


def _open_login_page(page: Page, base_url: str) -> None:
    open_vinted_login_page(page, base_url)


@dataclass(frozen=True, slots=True)
class VintedLoginResult:
    success: bool
    message: str
    storage_state: dict[str, Any] | None = None
    vinted_username: str | None = None


def _fill_login_form(page: Page, *, login: str, password: str) -> None:
    login_selectors = (
        "input[name='username']",
        "input[name='login']",
        "input[data-testid*='username']",
        "input[data-testid*='login']",
        "input[type='email']",
        "input[autocomplete='username']",
        "input[autocomplete='email']",
        "#username",
        "#login",
    )
    password_selectors = (
        "input[name='password']",
        "input[type='password']",
        "input[autocomplete='current-password']",
        "input[data-testid*='password']",
    )

    _accept_cookies(page)

    if not _set_input_value(page, login_selectors, login):
        raise RuntimeError(
            "Champ identifiant Vinted introuvable "
            "(souvent bloqué par le bandeau cookies — réessaie)."
        )

    password_visible = False
    for selector in password_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=800):
                password_visible = True
                break
        except Exception:
            continue
    if not password_visible:
        _submit_auth_form(page)
        page.wait_for_timeout(1000)
        _accept_cookies(page)

    if not _set_input_value(page, password_selectors, password):
        raise RuntimeError("Champ mot de passe Vinted introuvable.")

    _accept_cookies(page)
    password_field = page.locator("input[name='password']").first
    if not _submit_auth_form(page, password_field=password_field):
        raise RuntimeError("Bouton de connexion Vinted introuvable.")
    page.wait_for_timeout(2500)


def _submit_auth_form(page: Page, *, password_field: Any | None = None) -> bool:
    """Valide le formulaire login (Continuer) — JS / Enter plus fiables que Playwright click."""
    # 1) Bouton submit du formulaire qui contient username/password
    try:
        clicked = page.evaluate(
            """() => {
                const user = document.querySelector("input[name='username'], input[name='login'], input[type='email']");
                const form = user && user.closest('form');
                const btn = form && form.querySelector("button[type='submit'], button");
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if clicked:
            page.wait_for_timeout(800)
            return True
    except Exception:
        pass

    # 2) Sélecteurs Playwright (force)
    for selector in (
        "form:has(input[name='password']) button[type='submit']",
        "form:has(input[name='username']) button[type='submit']",
        "button[type='submit']:has-text('Continuer')",
        "button[type='submit']",
        "button:has-text('Continuer')",
        "button:has-text('Se connecter')",
        "button:has-text('Log in')",
    ):
        try:
            btn = page.locator(selector).first
            if btn.count() == 0:
                continue
            if btn.is_visible(timeout=1500):
                btn.click(timeout=5000, force=True)
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue

    # 3) Entrée dans le champ mot de passe
    try:
        target = password_field or page.locator("input[name='password']").first
        target.press("Enter")
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def _detect_login_error(page: Page) -> str | None:
    text = page.locator("body").inner_text(timeout=4000).lower()
    if any(marker in text for marker in BLOCK_MARKERS):
        return "Vinted a bloqué la connexion (activité inhabituelle). Réessaie plus tard."
    if any(
        marker in text
        for marker in (
            "identifiants incorrects",
            "mot de passe incorrect",
            "email ou mot de passe",
            "incorrect email",
            "invalid credentials",
            "n’est pas valide",
            "n'est pas valide",
        )
    ):
        return "Identifiants Vinted incorrects."
    if "mot de passe" in text and ("incorrect" in text or "invalid" in text or "erreur" in text):
        return "Identifiants Vinted incorrects."
    if "captcha" in text or "robot" in text or "datadome" in text:
        return "Vinted demande une vérification (captcha). Réessaie plus tard ou utilise la connexion manuelle."
    if "verification" in text and "code" in text:
        return "Vinted demande une vérification (2FA / SMS). Réessaie plus tard."
    return None


def _storage_has_access_token(storage_state: dict[str, Any]) -> bool:
    for cookie in storage_state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        if cookie.get("name") == "access_token_web" and cookie.get("value"):
            return True
    return False


def login_vinted_with_credentials(
    *,
    login: str,
    password: str,
    base_url: str,
    headless: bool = True,
) -> VintedLoginResult:
    """Connecte un compte Vinted et retourne la session Playwright."""
    base = base_url.rstrip("/")
    login_value = login.strip()
    if not login_value or not password:
        return VintedLoginResult(success=False, message="Email et mot de passe requis.")

    with sync_playwright() as playwright:
        browser = launch_vinted_browser(playwright, headless=headless)
        context = browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        apply_vinted_stealth(context)
        page = context.new_page()
        page.set_default_timeout(25_000)
        try:
            _open_login_page(page, base)
            _fill_login_form(page, login=login_value, password=password)

            # Attendre une vraie session membre (pas seulement un cookie guest)
            vinted_username: str | None = None
            for _ in range(20):
                page.wait_for_timeout(500)
                error = _detect_login_error(page)
                if error:
                    return VintedLoginResult(success=False, message=error)
                if _is_blocked_page(page):
                    return VintedLoginResult(
                        success=False,
                        message="Vinted a bloqué la session. Réessaie plus tard ou utilise la connexion manuelle.",
                    )
                user_block = _current_user_payload(page, base)
                if isinstance(user_block, dict):
                    login_name = str(
                        user_block.get("login") or user_block.get("username") or ""
                    ).strip()
                    if login_name:
                        vinted_username = login_name
                        break
                # Sortir de l'écran auth si besoin
                if "select_type" in page.url or "signup" in page.url.lower():
                    try:
                        page.goto(f"{base}/", wait_until="domcontentloaded", timeout=12_000)
                    except Exception:
                        pass

            if not vinted_username:
                error = _detect_login_error(page)
                return VintedLoginResult(
                    success=False,
                    message=error
                    or (
                        "Connexion Vinted non confirmée (captcha, 2FA ou identifiants). "
                        "Utilise la connexion manuelle (Chrome) sur /token/manual."
                    ),
                )

            storage_state = context.storage_state()
            if not _storage_has_access_token(storage_state):
                return VintedLoginResult(
                    success=False,
                    message="Connecté mais cookie access_token_web introuvable. Réessaie.",
                )

            log.info("vinted_login_success", username=vinted_username)
            return VintedLoginResult(
                success=True,
                message="Compte Vinted connecté.",
                storage_state=storage_state,
                vinted_username=vinted_username,
            )
        except Exception as exc:
            log.exception("vinted_login_failed", error=str(exc))
            return VintedLoginResult(
                success=False,
                message=f"Connexion impossible : {exc}",
            )
        finally:
            browser.close()
