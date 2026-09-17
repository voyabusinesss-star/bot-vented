"""Redeploy Railway après échecs Vinted persistants (403, thread limit).

Utilisé par bot-scrape (salons publics) et bot-detector (niches + fiches).
Chaque rôle a son checkpoint cooldown en DB (services indépendants).
"""

from __future__ import annotations

import time
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.db.repositories import get_checkpoint, set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.services.scrape_block_tracker import (
    consecutive_403_count,
    consecutive_thread_limit_count,
    recent_403_count,
    recent_thread_limit_count,
    tracker_snapshot,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_CHECKPOINT_KEYS: dict[str, str] = {
    "scrape": "scrape_ops:last_redeploy_at",
    "niches": "niches_ops:last_redeploy_at",
}
_SERVICE_LABELS: dict[str, str] = {
    "scrape": "bot-scrape",
    "niches": "bot-detector",
}
_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"
_REDEPLOY_MUTATION = """
mutation serviceInstanceRedeploy($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


def _checkpoint_key(role: str) -> str:
    return _CHECKPOINT_KEYS.get(role, f"{role}_ops:last_redeploy_at")


def _service_label(role: str, override: str | None = None) -> str:
    if override:
        return override
    return _SERVICE_LABELS.get(role, role)


def warn_if_proxy_blocks_redeploy(*, component: str) -> None:
    """Log explicite si auto-redeploy est armé mais neutralisé par un proxy."""
    settings = get_settings()
    if settings.scrape_auto_redeploy_enabled and settings.scrape_proxy_urls:
        log.warning(
            "auto_redeploy_disabled_by_proxy",
            component=component,
            hint="Retirer SCRAPE_PROXY_URLS pour activer le redeploy IP Railway",
        )


def _last_redeploy_ts(*, role: str = "scrape") -> float | None:
    key = _checkpoint_key(role)
    try:
        with session_scope() as session:
            data = get_checkpoint(session, key)
        if not data:
            return None
        raw = data.get("ts")
        return float(raw) if raw is not None else None
    except Exception:  # noqa: BLE001
        return None


def _persist_redeploy_ts(
    *,
    reason: str,
    role: str = "scrape",
    extra: dict[str, Any] | None = None,
) -> None:
    now = time.time()
    payload: dict[str, Any] = {
        "ts": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "reason": reason,
        "role": role,
    }
    if extra:
        payload.update(extra)
    with session_scope() as session:
        set_checkpoint(session, _checkpoint_key(role), payload)


def redeploy_cooldown_remaining(
    *,
    cooldown_seconds: float,
    role: str = "scrape",
) -> float:
    last = _last_redeploy_ts(role=role)
    if last is None:
        return 0.0
    remaining = cooldown_seconds - (time.time() - last)
    return max(0.0, remaining)


def trigger_service_redeploy(
    *,
    reason: str = "403",
    role: str = "scrape",
    extra: dict[str, Any] | None = None,
) -> bool:
    """Appelle l'API Railway. Retourne True si la mutation a répondu sans erreur GraphQL."""
    settings = get_settings()
    token = (settings.railway_api_token or "").strip()
    service_id = (settings.railway_service_id or "").strip()
    environment_id = (settings.railway_environment_id or "").strip()
    if not token or not service_id or not environment_id:
        log.warning(
            "railway_redeploy_skipped",
            reason="missing_credentials",
            role=role,
            has_token=bool(token),
            has_service=bool(service_id),
            has_environment=bool(environment_id),
        )
        return False

    import httpx

    headers = {
        "Project-Access-Token": token,
        "Content-Type": "application/json",
    }
    payload = {
        "query": _REDEPLOY_MUTATION,
        "variables": {
            "serviceId": service_id,
            "environmentId": environment_id,
        },
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(_GRAPHQL_URL, headers=headers, json=payload)
        body: dict[str, Any] = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        log.warning("railway_redeploy_failed", error=str(exc)[:200], role=role)
        return False

    if resp.status_code != 200:
        log.warning(
            "railway_redeploy_http_error",
            status=resp.status_code,
            body=str(resp.text)[:200],
            role=role,
        )
        return False
    if body.get("errors"):
        log.warning("railway_redeploy_graphql_error", errors=body.get("errors"), role=role)
        return False

    _persist_redeploy_ts(reason=reason, role=role, extra=extra)
    log.warning(
        "railway_redeploy_triggered",
        reason=reason,
        role=role,
        service_id=service_id,
        environment_id=environment_id,
        **(extra or {}),
    )
    return True


def _post_redeploy_alert(*, message: str, role: str) -> None:
    settings = get_settings()
    channel = (settings.discord_channel_logs or "").strip()
    token = (settings.discord_bot_token or "").strip()
    if not channel or not token:
        return
    titles = {
        "scrape": "Scrape public — auto-redeploy Railway",
        "niches": "Niches / fiches — auto-redeploy Railway",
    }
    title = titles.get(role, f"Vinted {role} — auto-redeploy Railway")
    try:
        import httpx

        url = f"https://discord.com/api/v10/channels/{channel}/messages"
        with httpx.Client(timeout=8.0) as client:
            client.post(
                url,
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "embeds": [
                        {
                            "title": title,
                            "description": message[:1800],
                            "color": 0xE74C3C,
                        }
                    ]
                },
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("railway_redeploy_alert_failed", error=str(exc)[:160])


def _auto_redeploy_common_checks(*, role: str = "scrape") -> float | None:
    settings = get_settings()
    if not settings.scrape_auto_redeploy_enabled:
        return None
    if settings.scrape_proxy_urls:
        return None
    cooldown = float(settings.scrape_auto_redeploy_cooldown_seconds)
    remaining = redeploy_cooldown_remaining(cooldown_seconds=cooldown, role=role)
    if remaining > 0:
        log.info(
            "scrape_auto_redeploy_cooldown",
            role=role,
            remaining_seconds=round(remaining, 1),
        )
        return None
    return cooldown


def maybe_auto_redeploy_on_scrape_failure(
    *,
    role: str = "scrape",
    service_label: str | None = None,
) -> bool:
    """Déclenche un redeploy si 403 ou thread limit persistants (sans proxy)."""
    if _auto_redeploy_common_checks(role=role) is None:
        return False

    label = _service_label(role, service_label)
    snap = tracker_snapshot()
    thread_threshold = int(get_settings().scrape_thread_redeploy_threshold)
    consecutive_thread = consecutive_thread_limit_count()
    recent_thread = recent_thread_limit_count(window_seconds=600.0)
    if consecutive_thread >= thread_threshold or recent_thread >= thread_threshold:
        log.warning(
            "scrape_thread_limit_threshold_reached",
            role=role,
            consecutive_thread_limit=consecutive_thread,
            recent_thread_limit_10m=recent_thread,
            threshold=thread_threshold,
            chromium_processes=snap.get("last_thread_limit_chrome_processes"),
            python_threads=snap.get("last_thread_limit_python_threads"),
        )
        _post_redeploy_alert(
            role=role,
            message=(
                f"**Thread limit Playwright** "
                f"(consecutive={consecutive_thread}, 10m={recent_thread}).\n"
                f"Chromium processes au crash: "
                f"`{snap.get('last_thread_limit_chrome_processes')}` — "
                f"Python threads: `{snap.get('last_thread_limit_python_threads')}`.\n"
                f"Redeploy Railway `{label}` pour repartir proprement."
            ),
        )
        return trigger_service_redeploy(
            reason="thread_limit_threshold",
            role=role,
            extra={
                "chromium_processes": snap.get("last_thread_limit_chrome_processes"),
                "python_threads": snap.get("last_thread_limit_python_threads"),
            },
        )

    threshold_403 = int(get_settings().scrape_403_redeploy_threshold)
    consecutive_403 = consecutive_403_count()
    recent_403 = recent_403_count(window_seconds=600.0)
    if consecutive_403 < threshold_403 and recent_403 < threshold_403:
        if consecutive_403 >= max(1, threshold_403 - 1):
            log.info(
                "scrape_auto_redeploy_pending",
                role=role,
                consecutive_403=consecutive_403,
                recent_403_10m=recent_403,
                threshold=threshold_403,
            )
        return False

    log.warning(
        "scrape_403_threshold_reached",
        role=role,
        consecutive_403=consecutive_403,
        recent_403_10m=recent_403,
        threshold=threshold_403,
    )
    resume = (
        "Le scrape repartira automatiquement au boot."
        if role == "scrape"
        else "Le détecteur + fiches reprendront via la boucle niches au boot."
    )
    _post_redeploy_alert(
        role=role,
        message=(
            f"**403 Vinted persistants** (consecutive={consecutive_403}, 10m={recent_403}).\n"
            f"Déclenchement redeploy Railway `{label}` pour tenter une nouvelle IP.\n"
            f"{resume}"
        ),
    )
    return trigger_service_redeploy(reason="403_threshold", role=role)


def maybe_auto_redeploy_on_403(
    *,
    role: str = "scrape",
    service_label: str | None = None,
) -> bool:
    """Alias historique — vérifie 403 et thread limit."""
    return maybe_auto_redeploy_on_scrape_failure(role=role, service_label=service_label)
