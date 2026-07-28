"""DM Resello unique : consultation filtres + activer/désactiver uniquement."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Sequence

from vinted_bot.config import get_settings
from vinted_bot.db.session import session_scope
from vinted_bot.db.user_filters import (
    get_or_create_member_plan,
    list_user_filters,
    plan_filter_limit,
    save_member_dm_dashboard,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2
ALERT_DM_TOGGLE_PREFIX = "alert:dm_toggle:"


def filter_pretty_name(row: Any) -> str:
    if getattr(row, "name", None) and str(row.name).strip():
        return str(row.name).strip()
    bits = [x for x in (getattr(row, "brand", None), getattr(row, "model", None)) if x]
    if bits:
        base = " ".join(str(x) for x in bits)
    elif getattr(row, "keyword", None):
        base = str(row.keyword)
    else:
        base = "Filtre"
    max_p = getattr(row, "max_price_eur", None)
    if max_p is not None:
        return f"{base} < {float(max_p):.0f} €"
    return base


def build_resello_dm_dashboard_payload(
    *,
    plan: str,
    filters: Sequence[Any],
    limit: int | None,
) -> dict[str, Any]:
    """Payload Discord du DM unique (embed + boutons pause/reprise)."""
    limit_txt = "∞" if limit is None else str(limit)
    plan_label = (plan or "starter").strip().upper()
    active_count = sum(1 for f in filters if bool(getattr(f, "is_active", False)))

    settings = get_settings()
    channel_id = (getattr(settings, "discord_channel_mes_alertes", "") or "").strip()
    manage_line = (
        f"⚙️ Gestion complète : <#{channel_id}>"
        if channel_id
        else "⚙️ Gestion complète : salon **MES ALERTES**"
    )

    parts: list[str] = [
        f"**Plan :** {plan_label}",
        f"**Filtres actifs :** {active_count}/{limit_txt}",
        "",
        "🔒 Tes recherches sont privées.",
        "",
        "━━━━━━━━━━━━━━",
    ]

    if not filters:
        parts.extend(
            [
                "",
                "_Aucun filtre pour l’instant._",
                "Crée tes alertes depuis le salon Discord **MES ALERTES**.",
                "",
                "━━━━━━━━━━━━━━",
            ]
        )
    else:
        for idx, row in enumerate(filters, start=1):
            name = filter_pretty_name(row)
            criteria: list[str] = []
            if row.brand:
                criteria.append(f"🏷️ {row.brand}")
            if row.model:
                criteria.append(f"🔎 {row.model}")
            if row.category:
                criteria.append(f"📂 {row.category}")
            if row.keyword:
                criteria.append(f"💬 {row.keyword}")
            if row.min_price_eur is not None:
                criteria.append(f"💰 Min : {float(row.min_price_eur):.0f} €")
            if row.max_price_eur is not None:
                criteria.append(f"💰 Max : {float(row.max_price_eur):.0f} €")
            active = bool(row.is_active)
            status = (
                "🟢 Surveillance active" if active else "🔴 Surveillance en pause"
            )
            parts.extend(
                [
                    "",
                    f"**#{idx} — Nom du filtre :**",
                    f"👟 **{name}**",
                    "",
                    "**Critères :**",
                    *(criteria if criteria else ["_Aucun_"]),
                    "",
                    "**Statut :**",
                    status,
                    "",
                    "━━━━━━━━━━━━━━",
                ]
            )

    parts.extend(
        [
            "",
            manage_line,
            "_Création / modification / suppression uniquement sur le serveur._",
        ]
    )

    description = "\n".join(parts)
    if len(description) > 4000:
        description = description[:3990] + "\n…"

    embed: dict[str, Any] = {
        "title": "🔔 TES FILTRES RESSELLO",
        "description": description,
        "color": EMBED_COLOR,
        "footer": {"text": "DM Resello · activer / désactiver uniquement"},
    }

    components: list[dict[str, Any]] = []
    # Max 5 rows × 5 boutons = 25
    buttons: list[dict[str, Any]] = []
    for idx, row in enumerate(filters[:25], start=1):
        active = bool(row.is_active)
        label = (
            f"⏸️ #{idx} Désactiver" if active else f"▶️ #{idx} Réactiver"
        )[:80]
        buttons.append(
            {
                "type": 2,
                "style": 2 if active else 3,
                "label": label,
                "custom_id": f"{ALERT_DM_TOGGLE_PREFIX}{int(row.id)}",
            }
        )
    for i in range(0, len(buttons), 5):
        components.append({"type": 1, "components": buttons[i : i + 5]})

    return {"embeds": [embed], "components": components}


def sync_resello_filters_dm(
    *,
    discord_user_id: int,
    discord_username: str | None = None,
) -> tuple[bool, str | None]:
    """Crée ou met à jour le DM dashboard unique. Retourne (ok, erreur)."""
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        return False, "token_missing"

    from vinted_bot.notify.discord import DiscordNotifier

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=discord_username,
        )
        filters = list_user_filters(session, discord_user_id)
        snapshot = [
            SimpleNamespace(
                id=int(f.id),
                name=f.name,
                brand=f.brand,
                model=f.model,
                category=f.category,
                keyword=f.keyword,
                min_price_eur=f.min_price_eur,
                max_price_eur=f.max_price_eur,
                is_active=bool(f.is_active),
            )
            for f in filters
        ]
        plan = plan_row.plan
        limit = plan_filter_limit(plan)
        dm_channel_id = (plan_row.dm_channel_id or "").strip() or None
        dm_message_id = (plan_row.dm_dashboard_message_id or "").strip() or None

    payload = build_resello_dm_dashboard_payload(
        plan=plan, filters=snapshot, limit=limit
    )

    try:
        with DiscordNotifier(settings) as notifier:
            channel_id = dm_channel_id or notifier.open_dm_channel(discord_user_id)
            updated = False
            if dm_channel_id and dm_message_id:
                try:
                    notifier.edit_message(dm_channel_id, dm_message_id, payload)
                    updated = True
                    channel_id, message_id = dm_channel_id, dm_message_id
                except Exception as exc:  # noqa: BLE001
                    log.info(
                        "resello_dm_edit_failed_repost",
                        discord_user_id=discord_user_id,
                        error=str(exc)[:160],
                    )
            if not updated:
                channel_id, message_id = notifier.send_dm_payload(
                    discord_user_id, payload
                )
            with session_scope() as session:
                save_member_dm_dashboard(
                    session,
                    discord_user_id,
                    dm_channel_id=channel_id,
                    dm_dashboard_message_id=message_id,
                    discord_username=discord_username,
                )
        return True, None
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:200]
        log.warning(
            "resello_dm_sync_failed",
            discord_user_id=discord_user_id,
            error=err,
        )
        return False, err


def parse_dm_toggle_filter_id(custom_id: str) -> int | None:
    if not custom_id.startswith(ALERT_DM_TOGGLE_PREFIX):
        return None
    try:
        return int(custom_id[len(ALERT_DM_TOGGLE_PREFIX) :])
    except ValueError:
        return None
