"""OAuth Vinted web — échange refresh_token → access_token."""

from __future__ import annotations

from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def exchange_refresh_token(
    refresh_token: str,
    *,
    base_url: str = "https://www.vinted.fr",
) -> dict[str, str]:
    """Retourne access_token + refresh_token (web client)."""
    import httpx

    token = (refresh_token or "").strip()
    if not token:
        raise ValueError("refresh_token vide")

    url = f"{base_url.rstrip('/')}/oauth/token"
    headers = {
        "User-Agent": _WEB_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    last_status: int | None = None
    for client_id in ("web", "ios"):
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": client_id,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "vinted_oauth_refresh_request_failed",
                client_id=client_id,
                error=str(exc)[:160],
            )
            continue
        last_status = int(resp.status_code)
        if resp.status_code != 200:
            log.warning(
                "vinted_oauth_refresh_http_error",
                client_id=client_id,
                status=resp.status_code,
                body=str(resp.text)[:200],
            )
            continue
        data = resp.json()
        access = str(data.get("access_token") or "").strip()
        if not access:
            continue
        new_refresh = str(data.get("refresh_token") or token).strip()
        log.info("vinted_oauth_refresh_ok", client_id=client_id)
        return {"access_token": access, "refresh_token": new_refresh}

    raise RuntimeError(
        f"Échec refresh OAuth Vinted (dernier status={last_status})"
    )


def storage_state_with_tokens(
    *,
    access_token: str,
    refresh_token: str | None = None,
    domain: str = ".vinted.fr",
) -> dict[str, Any]:
    cookies: list[dict[str, Any]] = [
        {
            "name": "access_token_web",
            "value": access_token,
            "domain": domain,
            "path": "/",
        }
    ]
    if refresh_token:
        cookies.append(
            {
                "name": "refresh_token_web",
                "value": refresh_token,
                "domain": domain,
                "path": "/",
            }
        )
    return {"cookies": cookies, "origins": []}
