"""Parse un token Vinted (v-tools / cookie) en Playwright storage_state."""

from __future__ import annotations

import json
import re
from typing import Any


class VintedTokenError(ValueError):
    """Token invalide ou format non reconnu."""


_ACCESS_TOKEN_RE = re.compile(
    r"(?:access_token_web\s*[=:]\s*)([^\s;\"']+)",
    re.IGNORECASE,
)


def _cookie(
    *,
    name: str,
    value: str,
    domain: str = ".vinted.fr",
    path: str = "/",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
    }


def storage_state_from_access_token(token: str) -> dict[str, Any]:
    value = (token or "").strip()
    if not value:
        raise VintedTokenError("Token vide.")
    return {"cookies": [_cookie(name="access_token_web", value=value)], "origins": []}


def parse_vinted_token_to_storage_state(raw: str) -> dict[str, Any]:
    """
    Accepte :
    - code session /token (sid) → storage_state complet en mémoire
    - valeur brute access_token_web
    - ligne access_token_web=... / cookie: access_token_web=...
    - JSON Playwright storage_state complet
    """
    text = (raw or "").strip()
    if not text:
        raise VintedTokenError("Colle ton token Vinted dans le formulaire.")

    # Code court généré par la page /token (session complète)
    try:
        from vinted_bot.services.token_capture import resolve_token_capture_storage

        captured = resolve_token_capture_storage(text)
        if captured is not None:
            return captured
    except Exception:
        pass

    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VintedTokenError("JSON invalide — recolle le token ou le storage_state.") from exc
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            cookies = [c for c in data["cookies"] if isinstance(c, dict) and c.get("name")]
            if not cookies:
                raise VintedTokenError("storage_state sans cookies utilisables.")
            out = dict(data)
            out["cookies"] = cookies
            if "origins" not in out:
                out["origins"] = []
            return out
        raise VintedTokenError("JSON non reconnu comme storage_state Playwright.")

    match = _ACCESS_TOKEN_RE.search(text)
    if match:
        return storage_state_from_access_token(match.group(1))

    # Valeur brute (souvent JWT / opaque token sans espaces)
    if "\n" in text or " " in text.strip():
        # parfois collé avec label sur la même ligne
        first = text.splitlines()[0].strip()
        match2 = _ACCESS_TOKEN_RE.search(first)
        if match2:
            return storage_state_from_access_token(match2.group(1))
        # token multi-ligne improbable → refuse
        if " " in text and "access_token" not in text.lower():
            raise VintedTokenError(
                "Format non reconnu. Colle le **code** de la page /token "
                "(recommandé) ou le token access_token_web."
            )

    cleaned = text.strip().strip('"').strip("'")
    if len(cleaned) < 8:
        raise VintedTokenError("Token trop court.")
    return storage_state_from_access_token(cleaned)
