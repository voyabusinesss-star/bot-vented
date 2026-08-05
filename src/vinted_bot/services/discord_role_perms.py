"""Copie les permission overwrites d'un rôle source vers un rôle cible (salons + catégories)."""

from __future__ import annotations

import time
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.interactions.discord_api import DiscordInteractionClient
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

# Bits utiles pour l'accès salon (sans droits admin que le bot n'a pas)
VIEW_CHANNEL = 1 << 10  # 1024
CONNECT = 1 << 20  # 1048576
# Discord refuse souvent de poser des allow que le bot n'a pas lui-même.
_ACCESS_ALLOW_MASK = VIEW_CHANNEL | CONNECT


def channel_ids_in_category(
    channels: list[dict[str, Any]],
    category_id: str,
) -> set[str]:
    """Catégorie + salons enfants directs."""
    cat = str(category_id or "").strip()
    if not cat:
        return set()
    out = {cat}
    for ch in channels:
        if str(ch.get("parent_id") or "") == cat:
            out.add(str(ch.get("id") or ""))
    out.discard("")
    return out


def _normalize_overwrite_bits(allow: str | int, deny: str | int) -> tuple[str, str]:
    """Garde l'intention d'accès Pro (voir/connecter) sans bits hors portée du bot."""
    try:
        allow_i = int(allow or 0)
    except (TypeError, ValueError):
        allow_i = 0
    try:
        deny_i = int(deny or 0)
    except (TypeError, ValueError):
        deny_i = 0
    # Si Pro peut voir le salon, Pro+ aussi (VIEW / CONNECT uniquement).
    # deny=0 : Discord refuse souvent de poser des deny bits hors portée du bot.
    if allow_i & VIEW_CHANNEL:
        allow_out = VIEW_CHANNEL | (allow_i & CONNECT)
    else:
        allow_out = allow_i & _ACCESS_ALLOW_MASK
    return str(allow_out), "0"


def copy_role_overwrites(
    *,
    source_role_id: str,
    target_role_id: str,
    guild_id: str | None = None,
    delay_seconds: float = 0.35,
    dry_run: bool = False,
    exclude_channel_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Pour chaque salon/catégorie qui a une overwrite du rôle source,
    applique la même (allow/deny) au rôle cible.
    """
    settings = get_settings()
    gid = (guild_id or settings.discord_guild_id or "").strip()
    src = str(source_role_id or "").strip()
    dst = str(target_role_id or "").strip()
    if not gid or not src or not dst:
        raise ValueError("guild_id / source_role_id / target_role_id requis")
    if src == dst:
        raise ValueError("source et cible identiques")

    stats = {
        "channels_scanned": 0,
        "copied": 0,
        "already_ok": 0,
        "skipped_no_source": 0,
        "skipped_excluded": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    excluded = {str(x).strip() for x in (exclude_channel_ids or set()) if str(x).strip()}

    with DiscordInteractionClient(settings) as client:
        channels = client.list_guild_channels(gid)
        for ch in channels:
            stats["channels_scanned"] += 1
            cid = str(ch.get("id") or "")
            if cid in excluded:
                stats["skipped_excluded"] += 1
                continue
            name = str(ch.get("name") or "")
            ctype = int(ch.get("type") or 0)
            overwrites = ch.get("permission_overwrites") or []
            if not isinstance(overwrites, list):
                continue

            src_ow: dict[str, Any] | None = None
            dst_ow: dict[str, Any] | None = None
            for ow in overwrites:
                if not isinstance(ow, dict):
                    continue
                oid = str(ow.get("id") or "")
                if oid == src and int(ow.get("type") or 0) == 0:
                    src_ow = ow
                if oid == dst and int(ow.get("type") or 0) == 0:
                    dst_ow = ow

            if src_ow is None:
                stats["skipped_no_source"] += 1
                continue

            allow, deny = _normalize_overwrite_bits(
                src_ow.get("allow") or "0",
                src_ow.get("deny") or "0",
            )
            if (
                dst_ow is not None
                and str(dst_ow.get("allow") or "0") == allow
                and str(dst_ow.get("deny") or "0") == deny
            ):
                stats["already_ok"] += 1
                continue

            log.info(
                "role_overwrite_copy",
                channel_id=cid,
                channel_name=name,
                channel_type=ctype,
                allow=allow,
                deny=deny,
                dry_run=dry_run,
            )
            if dry_run:
                stats["copied"] += 1
                continue
            try:
                client.edit_channel_permission(
                    cid,
                    dst,
                    allow=allow,
                    deny=deny,
                    overwrite_type=0,
                )
                stats["copied"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.warning(
                    "role_overwrite_copy_failed",
                    channel_id=cid,
                    channel_name=name,
                    error=str(exc)[:200],
                )
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    log.info("role_overwrite_copy_done", **stats)
    return stats


def deny_role_view_on_channels(
    *,
    role_id: str,
    channel_ids: set[str],
    guild_id: str | None = None,
    delay_seconds: float = 0.35,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Interdit VIEW (et CONNECT) au rôle sur les salons listés."""
    settings = get_settings()
    gid = (guild_id or settings.discord_guild_id or "").strip()
    rid = str(role_id or "").strip()
    if not gid or not rid:
        raise ValueError("guild_id / role_id requis")
    targets = {str(x).strip() for x in channel_ids if str(x).strip()}
    stats = {
        "channels_scanned": len(targets),
        "denied": 0,
        "already_ok": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    allow = "0"
    deny = str(VIEW_CHANNEL | CONNECT)

    with DiscordInteractionClient(settings) as client:
        channels = {str(c.get("id") or ""): c for c in client.list_guild_channels(gid)}
        for cid in sorted(targets):
            ch = channels.get(cid)
            name = str((ch or {}).get("name") or cid)
            dst_ow = None
            for ow in (ch or {}).get("permission_overwrites") or []:
                if str(ow.get("id") or "") == rid and int(ow.get("type") or 0) == 0:
                    dst_ow = ow
                    break
            if (
                dst_ow is not None
                and str(dst_ow.get("allow") or "0") == allow
                and str(dst_ow.get("deny") or "0") == deny
            ):
                stats["already_ok"] += 1
                continue
            log.info(
                "role_overwrite_deny_view",
                channel_id=cid,
                channel_name=name,
                dry_run=dry_run,
            )
            if dry_run:
                stats["denied"] += 1
                continue
            try:
                client.edit_channel_permission(
                    cid,
                    rid,
                    allow=allow,
                    deny=deny,
                    overwrite_type=0,
                )
                stats["denied"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.warning(
                    "role_overwrite_deny_view_failed",
                    channel_id=cid,
                    channel_name=name,
                    error=str(exc)[:200],
                )
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    log.info("role_overwrite_deny_view_done", **stats)
    return stats


def sync_starter_perms_from_pro(*, dry_run: bool = False) -> dict[str, Any]:
    """Copie les perms Resello Pro → Resello Starter (hors outils privés)."""
    settings = get_settings()
    pro = (settings.discord_role_sub_pro or "").strip()
    starter = (settings.discord_role_sub_starter or "").strip()
    private_cat = (settings.discord_category_private_tools or "").strip()
    if not pro or not starter:
        raise ValueError(
            "DISCORD_ROLE_SUB_PRO et DISCORD_ROLE_SUB_STARTER requis dans .env"
        )

    with DiscordInteractionClient(settings) as client:
        channels = client.list_guild_channels(settings.discord_guild_id or "")
    excluded = channel_ids_in_category(channels, private_cat) if private_cat else set()

    copy_stats = copy_role_overwrites(
        source_role_id=pro,
        target_role_id=starter,
        exclude_channel_ids=excluded,
        dry_run=dry_run,
    )
    deny_stats = {"denied": 0, "already_ok": 0, "errors": 0}
    if excluded:
        deny_stats = deny_role_view_on_channels(
            role_id=starter,
            channel_ids=excluded,
            dry_run=dry_run,
        )

    return {
        **copy_stats,
        "excluded_channels": len(excluded),
        "deny_view_denied": deny_stats.get("denied", 0),
        "deny_view_already_ok": deny_stats.get("already_ok", 0),
        "deny_view_errors": deny_stats.get("errors", 0),
    }


def sync_proplus_perms_from_pro(*, dry_run: bool = False) -> dict[str, Any]:
    """Copie les perms Resello Pro → Resello Pro+ sur tout le serveur."""
    settings = get_settings()
    pro = (settings.discord_role_sub_pro or "").strip()
    proplus = (settings.discord_role_sub_proplus or "").strip()
    if not pro or not proplus:
        raise ValueError(
            "DISCORD_ROLE_SUB_PRO et DISCORD_ROLE_SUB_PROPLUS requis dans .env"
        )
    return copy_role_overwrites(
        source_role_id=pro,
        target_role_id=proplus,
        dry_run=dry_run,
    )
