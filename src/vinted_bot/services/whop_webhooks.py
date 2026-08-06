"""Webhooks Whop — active/désactive abonnement + rôle Resello VIP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from vinted_bot.config import Settings, get_settings
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

# membership_id → payload partiel en attente d'un discord_user_id
_pending_claims: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()

# discord_user_id → timestamp acceptation règlement (filet auto-claim Whop)
_recent_reglement: dict[int, float] = {}
_recent_reglement_lock = threading.Lock()
_RECENT_REGLEMENT_TTL_SECONDS = 45 * 60

# Clics « Lien Starter/Pro/Pro+ » → (plan interne, timestamp) pour lier le webhook
_checkout_intents: dict[int, tuple[str, float]] = {}
_checkout_intents_lock = threading.Lock()
_CHECKOUT_INTENT_TTL_SECONDS = 45 * 60

_SIGNATURE_TOLERANCE_SECONDS = 300

_TIER_TO_INTERNAL_PLAN = {
    "starter": "starter",
    "pro": "premium",
    "premium": "premium",
    "proplus": "elite",
    "pro+": "elite",
    "elite": "elite",
}


def note_reglement_accepted(discord_user_id: int) -> None:
    """Mémorise un accept règlement récent (pour lier un paiement Whop sans Discord)."""
    try:
        uid = int(discord_user_id)
    except (TypeError, ValueError):
        return
    if uid <= 0:
        return
    with _recent_reglement_lock:
        now = time.time()
        _recent_reglement[uid] = now
        expired = [
            k
            for k, ts in _recent_reglement.items()
            if now - ts > _RECENT_REGLEMENT_TTL_SECONDS
        ]
        for k in expired:
            _recent_reglement.pop(k, None)


def recent_reglement_candidates(*, within_seconds: int | None = None) -> list[int]:
    """IDs Discord ayant accepté le règlement récemment (plus récent en premier)."""
    ttl = within_seconds or _RECENT_REGLEMENT_TTL_SECONDS
    now = time.time()
    with _recent_reglement_lock:
        items = [
            (uid, ts)
            for uid, ts in _recent_reglement.items()
            if now - ts <= ttl
        ]
    items.sort(key=lambda x: x[1], reverse=True)
    return [uid for uid, _ in items]


def normalize_checkout_plan(tier_or_plan: str) -> str:
    """starter/pro/proplus (boutons) → plan interne starter/premium/elite."""
    key = (tier_or_plan or "").strip().lower()
    return _TIER_TO_INTERNAL_PLAN.get(key, key)


def note_checkout_intent(discord_user_id: int, tier_or_plan: str) -> None:
    """Mémorise un clic Lien abo (parcours principal sans metadata Whop)."""
    try:
        uid = int(discord_user_id)
    except (TypeError, ValueError):
        return
    plan = normalize_checkout_plan(tier_or_plan)
    if uid <= 0 or plan not in {"starter", "premium", "elite"}:
        return
    with _checkout_intents_lock:
        now = time.time()
        _checkout_intents[uid] = (plan, now)
        expired = [
            k
            for k, (_p, ts) in _checkout_intents.items()
            if now - ts > _CHECKOUT_INTENT_TTL_SECONDS
        ]
        for k in expired:
            _checkout_intents.pop(k, None)
    log.info("whop_checkout_intent_noted", discord_user_id=uid, plan=plan)


def pop_checkout_intent(discord_user_id: int) -> str | None:
    with _checkout_intents_lock:
        row = _checkout_intents.pop(int(discord_user_id), None)
    return row[0] if row else None


def recent_checkout_intent_candidates(
    plan: str,
    *,
    within_seconds: int | None = None,
) -> list[int]:
    """IDs Discord ayant cliqué Lien pour ce plan récemment (plus récent en premier)."""
    want = normalize_checkout_plan(plan)
    ttl = within_seconds or _CHECKOUT_INTENT_TTL_SECONDS
    now = time.time()
    with _checkout_intents_lock:
        items = [
            (uid, ts)
            for uid, (p, ts) in _checkout_intents.items()
            if p == want and now - ts <= ttl
        ]
    items.sort(key=lambda x: x[1], reverse=True)
    return [uid for uid, _ in items]


def _plan_id_from_checkout_url(url: str) -> str:
    """Extrait plan_… depuis une URL checkout Whop (si présent)."""
    import re

    text = (url or "").strip()
    if not text:
        return ""
    match = re.search(r"(plan_[A-Za-z0-9]+)", text)
    return match.group(1) if match else ""


def product_plan_map(settings: Settings | None = None) -> dict[str, str]:
    """product_id / plan_id Whop → plan interne (starter/premium/elite)."""
    s = settings or get_settings()
    out: dict[str, str] = {}

    def _put(raw: str, plan: str) -> None:
        key = (raw or "").strip()
        if key:
            out[key] = plan

    def _get(name: str) -> str:
        return str(getattr(s, name, "") or "")

    _put(_get("whop_product_starter"), "starter")
    _put(_get("whop_product_pro"), "premium")
    _put(_get("whop_product_proplus"), "elite")
    _put(_get("whop_plan_starter"), "starter")
    _put(_get("whop_plan_pro"), "premium")
    _put(_get("whop_plan_proplus"), "elite")
    # Fallback : IDs plan dérivés des liens checkout Nos offres
    _put(_plan_id_from_checkout_url(_get("subscriptions_checkout_starter")), "starter")
    _put(_plan_id_from_checkout_url(_get("subscriptions_checkout_pro")), "premium")
    _put(_plan_id_from_checkout_url(_get("subscriptions_checkout_proplus")), "elite")
    return out


def plan_for_product_id(
    product_id: str | None,
    *,
    settings: Settings | None = None,
) -> str | None:
    pid = (product_id or "").strip()
    if not pid:
        return None
    return product_plan_map(settings).get(pid)


def _whop_hmac_keys(secret: str) -> list[bytes]:
    """Dérive les clés HMAC possibles (Whop ws_/whsec_ + variantes SDK)."""
    secret = (secret or "").strip()
    if not secret:
        return []
    keys: list[bytes] = []
    seen: set[bytes] = set()

    def _add(key: bytes) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    # Toujours tenter le secret brut (certains webhooks Whop signent ainsi).
    _add(secret.encode("utf-8"))

    if secret.startswith("whsec_"):
        raw = secret[len("whsec_") :]
        try:
            _add(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            pass
        _add(raw.encode("utf-8"))
    elif secret.startswith("ws_"):
        raw = secret[len("ws_") :]
        try:
            _add(bytes.fromhex(raw))
        except ValueError:
            pass
        try:
            _add(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            pass
        _add(raw.encode("utf-8"))
    else:
        try:
            _add(base64.b64decode(secret))
        except Exception:  # noqa: BLE001
            pass

    return keys


def verify_whop_signature(
    body: bytes,
    headers: dict[str, str],
    secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Vérifie la signature Standard Webhooks (comme le SDK Whop)."""
    secret = (secret or "").strip()
    if not secret:
        return False
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    msg_id = normalized.get("webhook-id") or ""
    timestamp = normalized.get("webhook-timestamp") or ""
    signature_header = normalized.get("webhook-signature") or ""
    if not msg_id or not timestamp or not signature_header:
        log.warning(
            "whop_webhook_signature_headers_missing",
            has_id=bool(msg_id),
            has_ts=bool(timestamp),
            has_sig=bool(signature_header),
        )
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    skew = abs(current - ts)
    if skew > _SIGNATURE_TOLERANCE_SECONDS:
        log.warning(
            "whop_webhook_signature_skew",
            skew_seconds=int(skew),
            tolerance=_SIGNATURE_TOLERANCE_SECONDS,
        )
        return False

    signed = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    candidates = [
        part[3:].strip()
        for part in signature_header.split(" ")
        if part.strip().startswith("v1,")
    ]
    if not candidates:
        log.warning("whop_webhook_signature_no_v1")
        return False

    for key in _whop_hmac_keys(secret):
        expected = base64.b64encode(
            hmac.new(key, signed, hashlib.sha256).digest()
        ).decode()
        for got in candidates:
            if hmac.compare_digest(got, expected):
                return True
    return False


def _dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def extract_product_id(data: dict[str, Any]) -> str | None:
    """Retourne un id utilisable pour le mapping (prod_… ou plan_…)."""
    for candidate in (
        _dig(data, "product", "id"),
        _dig(data, "product_id"),
        _dig(data, "plan", "product", "id"),
        _dig(data, "membership", "product", "id"),
        _dig(data, "plan", "id"),
        _dig(data, "plan_id"),
        _dig(data, "membership", "plan", "id"),
    ):
        if candidate:
            return str(candidate).strip()
    return None


def extract_membership_id(data: dict[str, Any]) -> str | None:
    for candidate in (
        data.get("id"),
        _dig(data, "membership", "id"),
        _dig(data, "membership_id"),
    ):
        if candidate and str(candidate).startswith("mem_"):
            return str(candidate).strip()
        if candidate and isinstance(candidate, str) and candidate.strip():
            # id racine du webhook membership.* est souvent mem_…
            text = candidate.strip()
            if text.startswith("mem_") or "membership" in str(data.get("id") or ""):
                return text
    mid = data.get("id")
    return str(mid).strip() if mid else None


def _parse_discord_snowflake(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if text.lower().startswith("discord:"):
        text = text.split(":", 1)[-1].strip()
    # Garde uniquement les chiffres (ex. "<@123>" / "id: 123")
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except (TypeError, ValueError):
        return None


def extract_discord_user_id(data: dict[str, Any]) -> int | None:
    """Essaie plusieurs formes de payload Whop / metadata / custom fields."""
    candidates: list[Any] = [
        _dig(data, "discord", "id"),
        _dig(data, "discord", "user_id"),
        _dig(data, "discord_account", "id"),
        _dig(data, "user", "discord", "id"),
        _dig(data, "user", "discord_id"),
        _dig(data, "user", "social_accounts", "discord", "id"),
        _dig(data, "member", "discord", "id"),
        _dig(data, "metadata", "discord_id"),
        _dig(data, "metadata", "discord_user_id"),
        _dig(data, "metadata", "discord"),
        data.get("discord_id"),
        data.get("discord_user_id"),
    ]
    socials = data.get("social_accounts")
    if isinstance(socials, list):
        for item in socials:
            if not isinstance(item, dict):
                continue
            service = str(item.get("service") or item.get("provider") or "").lower()
            if service == "discord":
                candidates.append(item.get("id") or item.get("account_id"))
    for raw in candidates:
        parsed = _parse_discord_snowflake(raw)
        if parsed is not None:
            return parsed

    # Custom checkout fields (question contenant "discord")
    responses = data.get("custom_field_responses")
    if isinstance(responses, list):
        for item in responses:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").lower()
            if "discord" not in question:
                continue
            parsed = _parse_discord_snowflake(item.get("answer"))
            if parsed is not None:
                return parsed
    return None


def fetch_whop_membership(
    membership_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """GET /memberships/{id} — enrichit le webhook si discord_id absent."""
    import httpx

    s = settings or get_settings()
    api_key = str(getattr(s, "whop_api_key", "") or "").strip()
    mid = (membership_id or "").strip()
    if not api_key or not mid:
        return None
    try:
        response = httpx.get(
            f"https://api.whop.com/api/v1/memberships/{mid}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "whop_membership_fetch_failed",
            membership_id=mid,
            error=str(exc)[:160],
        )
        return None
    if response.status_code >= 400:
        log.warning(
            "whop_membership_fetch_http",
            membership_id=mid,
            status=response.status_code,
            body=response.text[:160],
        )
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def resolve_discord_user_id(
    data: dict[str, Any],
    *,
    membership_id: str | None = None,
    settings: Settings | None = None,
) -> int | None:
    """Discord ID depuis le webhook, sinon via API Whop (compte / socials)."""
    found = extract_discord_user_id(data)
    if found is not None:
        return found
    mid = (membership_id or extract_membership_id(data) or "").strip()
    enriched = None
    if mid:
        enriched = fetch_whop_membership(mid, settings=settings)
        if enriched:
            found = extract_discord_user_id(enriched)
            if found is not None:
                return found
            user = enriched.get("user") if isinstance(enriched, dict) else None
            user_id = ""
            if isinstance(user, dict):
                user_id = str(user.get("id") or "").strip()
                found = extract_discord_user_id(user)
                if found is not None:
                    return found
            if user_id:
                profile = fetch_whop_user(user_id, settings=settings)
                if profile:
                    found = extract_discord_user_id(profile)
                    if found is not None:
                        return found
                    # social_accounts: [{service: discord, id: "..."}]
                    socials = profile.get("social_accounts")
                    if isinstance(socials, list):
                        for item in socials:
                            if not isinstance(item, dict):
                                continue
                            service = str(
                                item.get("service") or item.get("provider") or ""
                            ).lower()
                            if service != "discord":
                                continue
                            found = _parse_discord_snowflake(
                                item.get("id") or item.get("account_id")
                            )
                            if found is not None:
                                return found
    return None


def extract_email(data: dict[str, Any]) -> str | None:
    for candidate in (
        _dig(data, "user", "email"),
        _dig(data, "member", "user", "email"),
        _dig(data, "email"),
        _dig(data, "metadata", "email"),
    ):
        text = str(candidate or "").strip().lower()
        if text and "@" in text:
            return text
    return None


def extract_license_key(data: dict[str, Any]) -> str | None:
    for candidate in (
        data.get("license_key"),
        _dig(data, "membership", "license_key"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def store_pending_claim(
    membership_id: str,
    *,
    plan: str,
    product_id: str | None,
    email: str | None = None,
    license_key: str | None = None,
) -> None:
    """Mémoire + Postgres (survit aux redémarrages Railway)."""
    with _pending_lock:
        _pending_claims[membership_id] = {
            "plan": plan,
            "product_id": product_id,
            "email": email,
            "license_key": license_key,
            "stored_at": time.time(),
        }
    try:
        from vinted_bot.db.session import session_scope
        from vinted_bot.db.whop_claims import upsert_pending_claim

        with session_scope() as session:
            upsert_pending_claim(
                session,
                membership_id=membership_id,
                plan=plan,
                product_id=product_id,
                email=email,
                license_key=license_key,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "whop_pending_claim_db_failed",
            membership_id=membership_id,
            error=str(exc)[:160],
        )


def pop_pending_claim(membership_id: str) -> dict[str, Any] | None:
    with _pending_lock:
        return _pending_claims.pop(membership_id, None)


def _whop_api_headers(settings: Settings) -> dict[str, str] | None:
    api_key = str(getattr(settings, "whop_api_key", "") or "").strip()
    if not api_key:
        return None
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def resolve_whop_company_id(settings: Settings | None = None) -> str:
    """WHOP_COMPANY_ID ou dérivé du premier produit configuré."""
    import httpx

    s = settings or get_settings()
    configured = str(getattr(s, "whop_company_id", "") or "").strip()
    if configured:
        return configured
    headers = _whop_api_headers(s)
    if not headers:
        return ""
    for field in (
        "whop_product_pro",
        "whop_product_starter",
        "whop_product_proplus",
    ):
        pid = str(getattr(s, field, "") or "").strip()
        if not pid:
            continue
        try:
            response = httpx.get(
                f"https://api.whop.com/api/v1/products/{pid}",
                headers=headers,
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("whop_company_resolve_failed", error=str(exc)[:120])
            return ""
        if response.status_code >= 400:
            continue
        data = response.json() if response.content else {}
        company = data.get("company") if isinstance(data, dict) else None
        if isinstance(company, dict) and company.get("id"):
            return str(company["id"]).strip()
        if isinstance(data, dict) and data.get("company_id"):
            return str(data["company_id"]).strip()
    return ""


def resolve_whop_plan_id(
    tier: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Plan Whop pour un tier (env puis API, préfère renewal payant)."""
    import httpx

    s = settings or get_settings()
    tier_n = (tier or "").strip().lower()
    env_map = {
        "starter": str(getattr(s, "whop_plan_starter", "") or "").strip(),
        "pro": str(getattr(s, "whop_plan_pro", "") or "").strip(),
        "premium": str(getattr(s, "whop_plan_pro", "") or "").strip(),
        "proplus": str(getattr(s, "whop_plan_proplus", "") or "").strip(),
        "pro+": str(getattr(s, "whop_plan_proplus", "") or "").strip(),
        "elite": str(getattr(s, "whop_plan_proplus", "") or "").strip(),
    }
    if env_map.get(tier_n):
        return env_map[tier_n]

    product_map = {
        "starter": str(getattr(s, "whop_product_starter", "") or "").strip(),
        "pro": str(getattr(s, "whop_product_pro", "") or "").strip(),
        "premium": str(getattr(s, "whop_product_pro", "") or "").strip(),
        "proplus": str(getattr(s, "whop_product_proplus", "") or "").strip(),
        "pro+": str(getattr(s, "whop_product_proplus", "") or "").strip(),
        "elite": str(getattr(s, "whop_product_proplus", "") or "").strip(),
    }
    product_id = product_map.get(tier_n, "")
    company_id = resolve_whop_company_id(s)
    headers = _whop_api_headers(s)
    if not product_id or not company_id or not headers:
        return ""
    try:
        response = httpx.get(
            "https://api.whop.com/api/v1/plans",
            headers=headers,
            params={
                "company_id": company_id,
                "product_ids[]": product_id,
                "first": 50,
            },
            timeout=20.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_plan_resolve_failed", error=str(exc)[:120])
        return ""
    if response.status_code >= 400:
        log.warning(
            "whop_plan_resolve_http",
            status=response.status_code,
            body=response.text[:160],
        )
        return ""
    payload = response.json() if response.content else {}
    plans = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(plans, list) or not plans:
        return ""

    def _score(plan: dict[str, Any]) -> float:
        score = 0.0
        if str(plan.get("plan_type") or "") == "renewal":
            score += 100.0
        try:
            renew = float(plan.get("renewal_price") or 0)
        except (TypeError, ValueError):
            renew = 0.0
        try:
            initial = float(plan.get("initial_price") or 0)
        except (TypeError, ValueError):
            initial = 0.0
        if renew > 0:
            score += 50.0 + renew
        elif initial > 0:
            score += 20.0 + initial
        if str(plan.get("visibility") or "") == "visible":
            score += 10.0
        elif str(plan.get("visibility") or "") == "archived":
            score -= 30.0
        return score

    best = max(
        (p for p in plans if isinstance(p, dict) and p.get("id")),
        key=_score,
        default=None,
    )
    return str(best["id"]).strip() if best else ""


def fetch_whop_user(
    user_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    import httpx

    s = settings or get_settings()
    headers = _whop_api_headers(s)
    uid = (user_id or "").strip()
    if not headers or not uid:
        return None
    try:
        response = httpx.get(
            f"https://api.whop.com/api/v1/users/{uid}",
            headers=headers,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_user_fetch_failed", error=str(exc)[:120])
        return None
    if response.status_code >= 400:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def create_checkout_url_for_discord(
    *,
    tier: str,
    discord_user_id: int,
    settings: Settings | None = None,
) -> tuple[str | None, str | None]:
    """Crée un checkout Whop avec metadata discord_id (rôle auto au paiement).

    Retourne (url, erreur). Fallback URL statique si l'API échoue.
    """
    import httpx

    s = settings or get_settings()
    tier_n = (tier or "").strip().lower()
    static_by_tier = {
        "starter": str(getattr(s, "subscriptions_checkout_starter", "") or "").strip(),
        "pro": str(getattr(s, "subscriptions_checkout_pro", "") or "").strip(),
        "premium": str(getattr(s, "subscriptions_checkout_pro", "") or "").strip(),
        "proplus": str(getattr(s, "subscriptions_checkout_proplus", "") or "").strip(),
        "pro+": str(getattr(s, "subscriptions_checkout_proplus", "") or "").strip(),
        "elite": str(getattr(s, "subscriptions_checkout_proplus", "") or "").strip(),
    }
    static_url = static_by_tier.get(tier_n, "")
    if not static_url:
        plan_id_for_static = resolve_whop_plan_id(tier_n, settings=s)
        if plan_id_for_static:
            static_url = f"https://whop.com/checkout/{plan_id_for_static}"

    headers = _whop_api_headers(s)
    plan_id = resolve_whop_plan_id(tier_n, settings=s)

    # company_id ne doit PAS être envoyé avec une company API key (Whop 400).
    if headers and plan_id:
        try:
            response = httpx.post(
                "https://api.whop.com/api/v1/checkout_configurations",
                headers=headers,
                json={
                    "plan_id": plan_id,
                    "metadata": {
                        "discord_id": str(int(discord_user_id)),
                        "discord_user_id": str(int(discord_user_id)),
                    },
                },
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("whop_checkout_create_failed", error=str(exc)[:160])
            return static_url or None, str(exc)[:120]
        if response.status_code < 400:
            payload = response.json() if response.content else {}
            url = (
                (payload.get("purchase_url") if isinstance(payload, dict) else None)
                or None
            )
            if url:
                log.info(
                    "whop_checkout_personalized",
                    tier=tier_n,
                    discord_user_id=discord_user_id,
                    plan_id=plan_id,
                )
                return str(url), None
            plan_obj = payload.get("plan") if isinstance(payload, dict) else None
            pid = (
                plan_obj.get("id")
                if isinstance(plan_obj, dict)
                else None
            ) or plan_id
            session_id = payload.get("id") if isinstance(payload, dict) else None
            if session_id:
                return (
                    f"https://whop.com/checkout/{pid}?session={session_id}",
                    None,
                )
            return f"https://whop.com/checkout/{pid}", None
        log.warning(
            "whop_checkout_create_http",
            status=response.status_code,
            body=response.text[:200],
            plan_id=plan_id,
        )
        return static_url or None, f"Whop HTTP {response.status_code}"

    missing = []
    if not headers:
        missing.append("WHOP_API_KEY")
    if not plan_id:
        missing.append("plan_id")
    return static_url or None, "missing:" + ",".join(missing) if missing else None


def _activate_from_auto_claim(
    *,
    discord_user_id: int,
    membership_id: str | None,
    plan: str,
    product_id: str | None,
    email: str | None,
    license_key: str | None,
    settings: Settings,
    source: str,
) -> int:
    activate_subscription(
        discord_user_id=discord_user_id,
        plan=plan,
        membership_id=membership_id,
        settings=settings,
    )
    if membership_id:
        store_pending_claim(
            membership_id,
            plan=plan,
            product_id=product_id,
            email=email,
            license_key=license_key,
        )
        try:
            from vinted_bot.db.session import session_scope
            from vinted_bot.db.whop_claims import mark_claim_used

            with session_scope() as session:
                mark_claim_used(
                    session,
                    membership_id,
                    discord_user_id=discord_user_id,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("whop_auto_claim_mark_failed", error=str(exc)[:120], source=source)
        pop_pending_claim(membership_id)
    pop_checkout_intent(discord_user_id)
    log.info(
        "whop_auto_claim",
        source=source,
        discord_user_id=discord_user_id,
        membership_id=membership_id,
        plan=plan,
        email=email,
    )
    return discord_user_id


def try_auto_claim_from_checkout_intent(
    *,
    membership_id: str | None,
    plan: str,
    product_id: str | None,
    email: str | None = None,
    license_key: str | None = None,
    settings: Settings | None = None,
) -> int | None:
    """Si exactement 1 clic Lien récent pour ce plan → active le rôle sans metadata Whop."""
    from sqlalchemy import select

    from vinted_bot.db.models import DiscordMemberPlan
    from vinted_bot.db.session import session_scope

    s = settings or get_settings()
    candidates = recent_checkout_intent_candidates(plan, within_seconds=30 * 60)
    if not candidates:
        return None

    eligible: list[int] = []
    try:
        with session_scope() as session:
            for uid in candidates:
                row = session.scalar(
                    select(DiscordMemberPlan).where(
                        DiscordMemberPlan.discord_user_id == int(uid)
                    )
                )
                if row is not None and bool(getattr(row, "subscription_active", False)):
                    continue
                eligible.append(uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_checkout_intent_db_failed", error=str(exc)[:160])
        eligible = list(candidates)

    if len(eligible) != 1:
        log.info(
            "whop_checkout_intent_skip",
            reason="ambiguous_or_empty",
            candidates=len(candidates),
            eligible=len(eligible),
            membership_id=membership_id,
            plan=plan,
        )
        return None

    return _activate_from_auto_claim(
        discord_user_id=eligible[0],
        membership_id=membership_id,
        plan=plan,
        product_id=product_id,
        email=email,
        license_key=license_key,
        settings=s,
        source="checkout_intent",
    )


def try_auto_claim_from_recent_reglement(
    *,
    membership_id: str | None,
    plan: str,
    product_id: str | None,
    email: str | None = None,
    license_key: str | None = None,
    settings: Settings | None = None,
) -> int | None:
    """Si exactement 1 membre a accepté le règlement récemment sans abo → lien auto."""
    from sqlalchemy import select

    from vinted_bot.db.models import DiscordMemberPlan
    from vinted_bot.db.session import session_scope

    s = settings or get_settings()
    candidates = recent_reglement_candidates(within_seconds=30 * 60)
    if not candidates:
        return None

    eligible: list[int] = []
    try:
        with session_scope() as session:
            for uid in candidates:
                row = session.scalar(
                    select(DiscordMemberPlan).where(
                        DiscordMemberPlan.discord_user_id == int(uid)
                    )
                )
                if row is not None and bool(getattr(row, "subscription_active", False)):
                    continue
                eligible.append(uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_auto_claim_db_failed", error=str(exc)[:160])
        eligible = list(candidates)

    if len(eligible) != 1:
        log.info(
            "whop_auto_claim_skip",
            reason="ambiguous_or_empty",
            candidates=len(candidates),
            eligible=len(eligible),
            membership_id=membership_id,
        )
        return None

    return _activate_from_auto_claim(
        discord_user_id=eligible[0],
        membership_id=membership_id,
        plan=plan,
        product_id=product_id,
        email=email,
        license_key=license_key,
        settings=s,
        source="reglement",
    )


def claim_whop_access(
    *,
    discord_user_id: int,
    reference: str,
    discord_username: str | None = None,
    settings: Settings | None = None,
) -> tuple[bool, str]:
    """Lie un paiement Whop (email / mem_ / license) au compte Discord cliqueur."""
    from vinted_bot.db.session import session_scope
    from vinted_bot.db.whop_claims import find_open_claim, mark_claim_used

    s = settings or get_settings()
    ref = (reference or "").strip()
    if not ref:
        return False, "Référence vide."

    membership_id: str | None = None
    email: str | None = None
    license_key: str | None = None
    lower = ref.lower()
    if lower.startswith("mem_"):
        membership_id = ref
    elif "@" in ref:
        email = lower
    else:
        license_key = ref

    plan: str | None = None
    product_id: str | None = None
    found_mem: str | None = None

    with session_scope() as session:
        row = find_open_claim(
            session,
            membership_id=membership_id,
            email=email,
            license_key=license_key,
        )
        if row is not None:
            plan = row.plan
            product_id = row.product_id
            found_mem = row.membership_id

    if plan is None and membership_id:
        enriched = fetch_whop_membership(membership_id, settings=s)
        if enriched:
            product_id = extract_product_id(enriched) or product_id
            plan = plan_for_product_id(product_id, settings=s)
            found_mem = extract_membership_id(enriched) or membership_id
            # Persist for audit
            if plan and found_mem:
                store_pending_claim(
                    found_mem,
                    plan=plan,
                    product_id=product_id,
                    email=extract_email(enriched) or email,
                    license_key=extract_license_key(enriched) or license_key,
                )

    if not plan or not found_mem:
        return (
            False,
            "Aucun abonnement Whop trouvé pour cette référence.\n"
            "Vérifie l’email exact du paiement, ou colle ton `mem_…` "
            "(Whop → Manage membership).",
        )

    activate_subscription(
        discord_user_id=int(discord_user_id),
        plan=plan,
        membership_id=found_mem,
        discord_username=discord_username,
        settings=s,
    )
    try:
        with session_scope() as session:
            mark_claim_used(
                session,
                found_mem,
                discord_user_id=int(discord_user_id),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_claim_mark_failed", error=str(exc)[:120])
    pop_pending_claim(found_mem)

    plan_label = {
        "starter": "Starter",
        "premium": "Pro",
        "elite": "Pro+",
    }.get(plan, plan)
    return True, f"Accès **{plan_label}** activé. Ton rôle Discord est à jour."


def subscription_role_ids(settings: Settings | None = None) -> dict[str, str]:
    """plan interne → role_id Discord (vide si non configuré)."""
    s = settings or get_settings()
    return {
        "starter": (s.discord_role_sub_starter or "").strip(),
        "premium": (s.discord_role_sub_pro or "").strip(),
        "elite": (s.discord_role_sub_proplus or "").strip(),
    }


def all_subscription_role_ids(settings: Settings | None = None) -> list[str]:
    """Tous les rôles abo connus (tiers + VIP legacy)."""
    s = settings or get_settings()
    ids: list[str] = []
    for rid in subscription_role_ids(s).values():
        if rid and rid not in ids:
            ids.append(rid)
    vip = (s.discord_role_resello_vip or "").strip()
    if vip and vip not in ids:
        ids.append(vip)
    return ids


def roles_for_plan(plan: str, settings: Settings | None = None) -> list[str]:
    """Rôles à attribuer pour un plan — exclusifs (1 abo = 1 rôle).

    Starter → [starter]
    Pro (premium) → [pro]
    Pro+ (elite) → [proplus]

    Un changement d'abo remplace l'ancien rôle (pas d'empilement).
    Si aucun rôle tier n'est configuré, fallback sur DISCORD_ROLE_RESELLO_VIP.
    """
    from vinted_bot.db.user_filters import normalize_plan

    s = settings or get_settings()
    plan_n = normalize_plan(plan)
    roles_map = subscription_role_ids(s)
    rid = ""
    if plan_n == "starter":
        rid = roles_map["starter"]
    elif plan_n == "premium":
        rid = roles_map["premium"]
    elif plan_n == "elite":
        rid = roles_map["elite"]
    if rid:
        return [rid]
    vip = (s.discord_role_resello_vip or "").strip()
    return [vip] if vip else []


def sync_subscription_roles(
    *,
    discord_user_id: int,
    plan: str | None,
    active: bool,
    settings: Settings | None = None,
) -> None:
    """Aligne les rôles Discord sur le plan (retire les autres rôles abo)."""
    from vinted_bot.interactions.discord_api import DiscordInteractionClient

    s = settings or get_settings()
    guild_id = (s.discord_guild_id or "").strip()
    if not guild_id or not s.discord_bot_token.strip():
        log.warning(
            "whop_roles_skip",
            reason="missing_guild_or_token",
            discord_user_id=discord_user_id,
            active=active,
        )
        return

    wanted = roles_for_plan(plan or "starter", s) if active else []
    all_roles = all_subscription_role_ids(s)
    if not all_roles and not wanted:
        log.warning(
            "whop_roles_skip",
            reason="no_roles_configured",
            discord_user_id=discord_user_id,
        )
        return

    with DiscordInteractionClient(s) as client:
        for rid in all_roles:
            if rid in wanted:
                continue
            try:
                client.remove_guild_member_role(guild_id, discord_user_id, rid)
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "whop_role_remove_skip",
                    role_id=rid,
                    error=str(exc)[:120],
                )
        for rid in wanted:
            try:
                client.add_guild_member_role(guild_id, discord_user_id, rid)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "whop_role_add_failed",
                    role_id=rid,
                    discord_user_id=discord_user_id,
                    error=str(exc)[:160],
                )
    log.info(
        "whop_roles_synced",
        discord_user_id=discord_user_id,
        plan=plan,
        active=active,
        roles=wanted,
    )


def sync_vip_role(
    *,
    discord_user_id: int,
    grant: bool,
    plan: str = "premium",
    settings: Settings | None = None,
) -> None:
    """Compat : délègue à sync_subscription_roles."""
    sync_subscription_roles(
        discord_user_id=discord_user_id,
        plan=plan if grant else "starter",
        active=grant,
        settings=settings,
    )


def activate_subscription(
    *,
    discord_user_id: int,
    plan: str,
    membership_id: str | None = None,
    discord_username: str | None = None,
    settings: Settings | None = None,
) -> None:
    from vinted_bot.db.session import session_scope
    from vinted_bot.db.user_filters import set_member_plan

    with session_scope() as session:
        set_member_plan(
            session,
            discord_user_id,
            plan,
            discord_username=discord_username,
            subscription_active=True,
            whop_membership_id=membership_id,
        )
    sync_subscription_roles(
        discord_user_id=discord_user_id,
        plan=plan,
        active=True,
        settings=settings,
    )
    log.info(
        "whop_subscription_activated",
        discord_user_id=discord_user_id,
        plan=plan,
        membership_id=membership_id,
    )


def deactivate_subscription(
    *,
    discord_user_id: int,
    membership_id: str | None = None,
    settings: Settings | None = None,
) -> None:
    from vinted_bot.db.session import session_scope
    from vinted_bot.db.user_filters import (
        deactivate_all_user_filters,
        set_member_plan,
    )

    with session_scope() as session:
        set_member_plan(
            session,
            discord_user_id,
            "starter",
            subscription_active=False,
            whop_membership_id=membership_id,
        )
        n = deactivate_all_user_filters(session, discord_user_id)
    sync_subscription_roles(
        discord_user_id=discord_user_id,
        plan="starter",
        active=False,
        settings=settings,
    )
    log.info(
        "whop_subscription_deactivated",
        discord_user_id=discord_user_id,
        filters_paused=n,
        membership_id=membership_id,
    )


_ACTIVATE_EVENTS = {
    "membership.activated",
    "membership_activated",
    "membership.went_valid",
    "membership_went_valid",
}
_DEACTIVATE_EVENTS = {
    "membership.deactivated",
    "membership_deactivated",
    "membership.went_invalid",
    "membership_went_invalid",
}


def handle_whop_event(
    event_type: str,
    data: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> str:
    """Traite un event Whop. Retourne un statut court pour les logs."""
    s = settings or get_settings()
    product_id = extract_product_id(data)
    membership_id = extract_membership_id(data)
    discord_user_id = extract_discord_user_id(data)

    if event_type in _ACTIVATE_EVENTS:
        plan = plan_for_product_id(product_id, settings=s)
        if plan is None:
            log.warning(
                "whop_unknown_product",
                product_id=product_id,
                membership_id=membership_id,
                known_ids=sorted(product_plan_map(s).keys()),
            )
            return "unknown_product"
        if discord_user_id is None:
            discord_user_id = resolve_discord_user_id(
                data,
                membership_id=membership_id,
                settings=s,
            )
        if discord_user_id is None:
            email = extract_email(data)
            license_key = extract_license_key(data)
            if membership_id:
                store_pending_claim(
                    membership_id,
                    plan=plan,
                    product_id=product_id,
                    email=email,
                    license_key=license_key,
                )
            auto_uid = try_auto_claim_from_checkout_intent(
                membership_id=membership_id,
                plan=plan,
                product_id=product_id,
                email=email,
                license_key=license_key,
                settings=s,
            )
            if auto_uid is not None:
                return "activated_auto_checkout"
            auto_uid = try_auto_claim_from_recent_reglement(
                membership_id=membership_id,
                plan=plan,
                product_id=product_id,
                email=email,
                license_key=license_key,
                settings=s,
            )
            if auto_uid is not None:
                return "activated_auto_reglement"
            log.warning(
                "whop_missing_discord_id",
                membership_id=membership_id,
                product_id=product_id,
                plan=plan,
                email=email,
            )
            return "pending_discord"
        activate_subscription(
            discord_user_id=discord_user_id,
            plan=plan,
            membership_id=membership_id,
            settings=s,
        )
        return "activated"

    if event_type in _DEACTIVATE_EVENTS:
        from vinted_bot.db.models import DiscordMemberPlan
        from vinted_bot.db.session import session_scope
        from sqlalchemy import select

        row = None
        with session_scope() as session:
            if membership_id:
                row = session.scalar(
                    select(DiscordMemberPlan).where(
                        DiscordMemberPlan.whop_membership_id == membership_id
                    )
                )
            if row is None and discord_user_id is not None:
                row = session.scalar(
                    select(DiscordMemberPlan).where(
                        DiscordMemberPlan.discord_user_id == int(discord_user_id)
                    )
                )
            if row is not None:
                discord_user_id = int(row.discord_user_id)
                current_mem = (row.whop_membership_id or "").strip() or None
                # Upgrade Pro → Pro+ : l'ancien membership se coupe mais
                # le membre a déjà un nouvel abo — ne pas tout retirer.
                if (
                    membership_id
                    and current_mem
                    and current_mem != membership_id
                ):
                    log.info(
                        "whop_deactivate_skip_stale_membership",
                        membership_id=membership_id,
                        current_membership_id=current_mem,
                        discord_user_id=discord_user_id,
                    )
                    return "deactivate_skip_stale"
        if discord_user_id is None:
            log.warning(
                "whop_deactivate_no_discord",
                membership_id=membership_id,
            )
            return "deactivate_skip"
        deactivate_subscription(
            discord_user_id=discord_user_id,
            membership_id=membership_id,
            settings=s,
        )
        return "deactivated"

    log.info("whop_event_ignored", event_type=event_type)
    return "ignored"


def parse_whop_envelope(body: bytes) -> tuple[str, dict[str, Any]]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("payload non-objet")
    event_type = str(
        payload.get("type") or payload.get("action") or payload.get("event") or ""
    ).strip()
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    return event_type, data


class _WhopWebhookHandler(BaseHTTPRequestHandler):
    settings: Settings

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        log.debug("whop_http", message=fmt % args)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _send(self, code: int, text: str = "OK") -> None:
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/health", "/webhooks/whop/health", "/"}:
            from vinted_bot.services.scrape_heartbeat import scrape_health_line

            self._send(200, f"ok {scrape_health_line()}")
            return
        if path == "/health/scrape":
            from vinted_bot.services.scrape_heartbeat import read_scrape_heartbeat

            data = read_scrape_heartbeat()
            if not data:
                self._send(503, "scrape heartbeat missing")
                return
            self._send(200, json.dumps(data, ensure_ascii=False))
            return
        self._send(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/webhooks/whop":
            self._send(404, "not found")
            return
        body = self._read_body()
        headers = {k: v for k, v in self.headers.items()}
        secret = (self.settings.whop_webhook_secret or "").strip()
        if secret and not verify_whop_signature(body, headers, secret):
            log.warning("whop_webhook_bad_signature")
            self._send(401, "invalid signature")
            return
        if not secret:
            log.warning("whop_webhook_secret_missing_accepting")
        try:
            event_type, data = parse_whop_envelope(body)
        except Exception as exc:  # noqa: BLE001
            log.warning("whop_webhook_bad_json", error=str(exc)[:120])
            self._send(400, "bad json")
            return
        try:
            status = handle_whop_event(event_type, data, settings=self.settings)
            log.info(
                "whop_webhook_handled",
                event_type=event_type,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("whop_webhook_handler_failed", error=str(exc)[:200])
            # 200 pour éviter storm de retries sur bug métier
            self._send(200, "error_logged")
            return
        self._send(200, "OK")


def start_whop_webhook_server(
    settings: Settings | None = None,
) -> ThreadingHTTPServer | None:
    """Démarre le serveur HTTP Whop en thread daemon.

    Toujours démarré (healthcheck Railway sur PORT), même sans secret Whop.
    """
    s = settings or get_settings()
    host = (s.whop_webhook_host or "0.0.0.0").strip() or "0.0.0.0"
    port = s.effective_whop_webhook_port()

    handler = type(
        "BoundWhopWebhookHandler",
        (_WhopWebhookHandler,),
        {"settings": s},
    )
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        log.error(
            "whop_webhook_server_bind_failed",
            host=host,
            port=port,
            error=str(exc)[:200],
        )
        return None
    thread = threading.Thread(
        target=server.serve_forever,
        name="whop-webhook-http",
        daemon=True,
    )
    thread.start()
    configured = bool(
        (s.whop_webhook_secret or "").strip()
        or (s.discord_role_sub_starter or "").strip()
        or (s.discord_role_sub_pro or "").strip()
        or product_plan_map(s)
    )
    log.info(
        "whop_webhook_server_start",
        host=host,
        port=port,
        whop_configured=configured,
    )
    # Smoke check local — utile dans les logs Railway
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as resp:
            log.info("whop_webhook_self_check", status=resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("whop_webhook_self_check_failed", error=str(exc)[:160])
    return server
