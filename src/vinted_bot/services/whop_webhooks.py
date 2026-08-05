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

_SIGNATURE_TOLERANCE_SECONDS = 300


def product_plan_map(settings: Settings | None = None) -> dict[str, str]:
    """product_id Whop → plan interne (starter/premium/elite)."""
    s = settings or get_settings()
    out: dict[str, str] = {}
    starter = (s.whop_product_starter or "").strip()
    pro = (s.whop_product_pro or "").strip()
    proplus = (s.whop_product_proplus or "").strip()
    if starter:
        out[starter] = "starter"
    if pro:
        out[pro] = "premium"
    if proplus:
        out[proplus] = "elite"
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
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > _SIGNATURE_TOLERANCE_SECONDS:
        return False

    # Whop : whsec_ (prod) = clé base64 ; ws_ (sandbox) = clé hex après le préfixe.
    key = secret.encode("utf-8")
    if secret.startswith("whsec_"):
        raw = secret[len("whsec_") :]
        try:
            key = base64.b64decode(raw)
        except Exception:  # noqa: BLE001
            key = secret.encode("utf-8")
    elif secret.startswith("ws_"):
        raw = secret[len("ws_") :]
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            key = secret.encode("utf-8")

    signed = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    for part in signature_header.split(" "):
        part = part.strip()
        if not part.startswith("v1,"):
            continue
        got = part[3:]
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
    for candidate in (
        _dig(data, "product", "id"),
        _dig(data, "product_id"),
        _dig(data, "plan", "product", "id"),
        _dig(data, "membership", "product", "id"),
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


def extract_discord_user_id(data: dict[str, Any]) -> int | None:
    """Essaie plusieurs formes de payload Whop / metadata."""
    candidates: list[Any] = [
        _dig(data, "discord", "id"),
        _dig(data, "discord", "user_id"),
        _dig(data, "discord_account", "id"),
        _dig(data, "user", "discord", "id"),
        _dig(data, "user", "discord_id"),
        _dig(data, "metadata", "discord_id"),
        _dig(data, "metadata", "discord_user_id"),
        data.get("discord_id"),
        data.get("discord_user_id"),
    ]
    for raw in candidates:
        if raw is None or raw == "":
            continue
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return None


def store_pending_claim(
    membership_id: str,
    *,
    plan: str,
    product_id: str | None,
) -> None:
    with _pending_lock:
        _pending_claims[membership_id] = {
            "plan": plan,
            "product_id": product_id,
            "stored_at": time.time(),
        }


def pop_pending_claim(membership_id: str) -> dict[str, Any] | None:
    with _pending_lock:
        return _pending_claims.pop(membership_id, None)


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
    """Rôles à attribuer pour un plan (empilés : Pro ⊂ Pro+).

    Starter → [starter]
    Pro (premium) → [starter, pro]
    Pro+ (elite) → [starter, pro, proplus]

    Si aucun rôle tier n'est configuré, fallback sur DISCORD_ROLE_RESELLO_VIP.
    """
    from vinted_bot.db.user_filters import normalize_plan

    s = settings or get_settings()
    plan_n = normalize_plan(plan)
    roles_map = subscription_role_ids(s)
    stack: list[str] = []
    if plan_n in {"starter", "premium", "elite"} and roles_map["starter"]:
        stack.append(roles_map["starter"])
    if plan_n in {"premium", "elite"} and roles_map["premium"]:
        stack.append(roles_map["premium"])
    if plan_n == "elite" and roles_map["elite"]:
        stack.append(roles_map["elite"])
    # Dédup en gardant l'ordre
    out: list[str] = []
    for rid in stack:
        if rid and rid not in out:
            out.append(rid)
    if out:
        return out
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
            )
            return "unknown_product"
        if discord_user_id is None:
            if membership_id:
                store_pending_claim(
                    membership_id, plan=plan, product_id=product_id
                )
            log.warning(
                "whop_missing_discord_id",
                membership_id=membership_id,
                product_id=product_id,
                plan=plan,
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
        if discord_user_id is None and membership_id:
            # Lookup DB by membership id
            from vinted_bot.db.models import DiscordMemberPlan
            from vinted_bot.db.session import session_scope
            from sqlalchemy import select

            with session_scope() as session:
                row = session.scalar(
                    select(DiscordMemberPlan).where(
                        DiscordMemberPlan.whop_membership_id == membership_id
                    )
                )
                if row is not None:
                    discord_user_id = int(row.discord_user_id)
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
        if path in {"/health", "/webhooks/whop/health"}:
            self._send(200, "ok")
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
    """Démarre le serveur HTTP Whop en thread daemon."""
    s = settings or get_settings()
    host = (s.whop_webhook_host or "0.0.0.0").strip() or "0.0.0.0"
    port = s.effective_whop_webhook_port()
    # Toujours démarrer si VIP / secret / produits configurés
    if not any(
        [
            (s.whop_webhook_secret or "").strip(),
            (s.discord_role_resello_vip or "").strip(),
            (s.discord_role_sub_starter or "").strip(),
            (s.discord_role_sub_pro or "").strip(),
            (s.discord_role_sub_proplus or "").strip(),
            product_plan_map(s),
        ]
    ):
        log.info("whop_webhook_server_skip", reason="not_configured")
        return None

    handler = type(
        "BoundWhopWebhookHandler",
        (_WhopWebhookHandler,),
        {"settings": s},
    )
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="whop-webhook-http",
        daemon=True,
    )
    thread.start()
    log.info("whop_webhook_server_start", host=host, port=port)
    return server
