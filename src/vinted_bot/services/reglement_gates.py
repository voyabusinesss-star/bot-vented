"""Verrouillage des salons jusqu'à acceptation du règlement."""

from __future__ import annotations

from typing import Any

from vinted_bot.config import sanitize_discord_channel_id
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

VIEW_CHANNEL = 1 << 10
TEXT_CHANNEL_TYPES = {0, 5}
GATE_CHANNEL_TYPES = {0, 2, 5}
CATEGORY_TYPE = 4
MEMBRE_ROLE_NAME = "Membre"


def _parse_perm(value: str | int | None) -> int:
    if value is None:
        return 0
    return int(value)


def merge_view_channel_overwrite(
    overwrites: list[dict[str, Any]],
    *,
    target_id: str,
    target_type: int,
    allow_view: bool,
) -> list[dict[str, Any]]:
    """Fusionne une overwrite Voir le salon (VIEW_CHANNEL)."""
    result = [
        dict(ow)
        for ow in overwrites
        if not (str(ow.get("id")) == target_id and int(ow.get("type", 0)) == target_type)
    ]
    allow = VIEW_CHANNEL if allow_view else 0
    deny = 0 if allow_view else VIEW_CHANNEL
    result.append(
        {
            "id": target_id,
            "type": target_type,
            "allow": str(allow),
            "deny": str(deny),
        }
    )
    return result


def _allow_view_for_roles(
    overwrites: list[dict[str, Any]],
    role_ids: list[str],
) -> list[dict[str, Any]]:
    result = list(overwrites)
    for role_id in role_ids:
        if role_id:
            result = merge_view_channel_overwrite(
                result,
                target_id=role_id,
                target_type=0,
                allow_view=True,
            )
    return result


def build_gated_overwrites(
    existing: list[dict[str, Any]],
    *,
    everyone_id: str,
    member_role_id: str,
    is_public: bool,
    bot_role_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Salon public (bienvenue/règlement) ou réservé au rôle Membre."""
    keep_bot = list(bot_role_ids or [])
    overwrites = list(existing or [])
    if is_public:
        overwrites = merge_view_channel_overwrite(
            overwrites,
            target_id=everyone_id,
            target_type=0,
            allow_view=True,
        )
        overwrites = _allow_view_for_roles(overwrites, [member_role_id, *keep_bot])
        return overwrites
    overwrites = merge_view_channel_overwrite(
        overwrites,
        target_id=everyone_id,
        target_type=0,
        allow_view=False,
    )
    overwrites = _allow_view_for_roles(overwrites, [member_role_id, *keep_bot])
    return overwrites


def find_role_by_name(roles: list[dict[str, Any]], name: str) -> str:
    target = name.strip().lower()
    for role in roles:
        if str(role.get("name") or "").strip().lower() == target:
            return str(role["id"])
    return ""


def ensure_membre_role(client: Any, guild_id: str) -> str:
    """Retourne l'ID du rôle Membre (existant ou créé)."""
    from vinted_bot.config import get_settings

    settings = get_settings()
    configured = client.reglement_verified_role_id()
    if configured:
        return configured

    response = client._client.get(f"/guilds/{guild_id}/roles")
    if response.status_code >= 400:
        raise RuntimeError(
            f"List roles {response.status_code}: {response.text[:400]}"
        )
    roles = response.json()
    existing = find_role_by_name(roles, MEMBRE_ROLE_NAME)
    if existing:
        log.info("reglement_role_found", role_id=existing, name=MEMBRE_ROLE_NAME)
        return existing

    create = client._client.post(
        f"/guilds/{guild_id}/roles",
        json={
            "name": MEMBRE_ROLE_NAME,
            "color": 0x5865F2,
            "hoist": False,
            "mentionable": False,
        },
    )
    if create.status_code >= 400:
        raise RuntimeError(
            f"Create role {create.status_code}: {create.text[:400]}\n"
            "Crée manuellement un rôle « Membre » et renseigne "
            "DISCORD_ROLE_REGLEMENT_VERIFIED dans .env."
        )
    role_id = str(create.json()["id"])
    log.info("reglement_role_created", role_id=role_id, name=MEMBRE_ROLE_NAME)
    return role_id


def resolve_public_channel_ids(settings: Any) -> set[str]:
    public: set[str] = set()
    for field in (
        "discord_channel_bienvenue",
        "discord_channel_reglement",
        "discord_channel_presentation",
        "discord_channel_annonces",
        "discord_channel_concours",
    ):
        cid = sanitize_discord_channel_id(getattr(settings, field, "") or "")
        if cid:
            public.add(cid)
    return public


def find_bot_role_ids(client: Any, guild_id: str) -> list[str]:
    """Rôles du bot à préserver (accès + gestion des salons)."""
    from vinted_bot.config import discord_application_id

    token = getattr(client.settings, "discord_bot_token", "") or ""
    bot_user_id = discord_application_id(token)
    if not bot_user_id:
        return []
    response = client._client.get(f"/guilds/{guild_id}/members/{bot_user_id}")
    if response.status_code >= 400:
        return []
    roles = [str(r) for r in (response.json().get("roles") or []) if str(r)]
    # Préférer le rôle nommé Resello si présent
    named: list[str] = []
    roles_resp = client._client.get(f"/guilds/{guild_id}/roles")
    if roles_resp.status_code == 200:
        by_id = {str(r["id"]): str(r.get("name") or "") for r in roles_resp.json()}
        for rid in roles:
            if by_id.get(rid, "").lower() == "resello":
                named.append(rid)
    return named or roles


def apply_reglement_gates(
    client: Any,
    *,
    guild_id: str,
    member_role_id: str,
    public_channel_ids: set[str],
    dry_run: bool = False,
) -> dict[str, int]:
    """Masque tous les salons sauf bienvenue/règlement pour @everyone."""
    everyone_id = guild_id
    bot_role_ids = find_bot_role_ids(client, guild_id)
    response = client._client.get(f"/guilds/{guild_id}/channels")
    if response.status_code >= 400:
        raise RuntimeError(
            f"List channels {response.status_code}: {response.text[:400]}"
        )

    stats = {"updated": 0, "skipped": 0, "public": 0, "gated": 0, "failed": 0}
    channels = response.json()

    def _patch_channel(channel_id: str, is_public: bool, *, name: str = "") -> None:
        detail = client._client.get(f"/channels/{channel_id}")
        existing: list[dict[str, Any]] = []
        if detail.status_code == 200:
            data = detail.json()
            existing = data.get("permission_overwrites") or []
            name = name or str(data.get("name") or "")
        elif detail.status_code != 403:
            stats["skipped"] += 1
            return

        overwrites = build_gated_overwrites(
            existing,
            everyone_id=everyone_id,
            member_role_id=member_role_id,
            is_public=is_public,
            bot_role_ids=bot_role_ids,
        )
        if dry_run:
            stats["updated"] += 1
            if is_public:
                stats["public"] += 1
            else:
                stats["gated"] += 1
            return

        patch = client._client.patch(
            f"/channels/{channel_id}",
            json={"permission_overwrites": overwrites},
        )
        if patch.status_code >= 400:
            log.warning(
                "reglement_gate_failed",
                channel_id=channel_id,
                name=name,
                error=patch.text[:200],
            )
            stats["failed"] += 1
            return

        stats["updated"] += 1
        if is_public:
            stats["public"] += 1
        else:
            stats["gated"] += 1
        log.info(
            "reglement_gate_applied",
            channel_id=channel_id,
            name=name,
            public=is_public,
        )

    public_ids = {str(ch_id) for ch_id in public_channel_ids}

    # 1) Verrouiller les catégories (sauf celles qui contiennent bienvenue/règlement)
    for ch in channels:
        if int(ch.get("type", 0)) != CATEGORY_TYPE:
            continue
        cat_id = str(ch["id"])
        children = [c for c in channels if str(c.get("parent_id") or "") == cat_id]
        if any(str(c["id"]) in public_ids for c in children):
            continue
        _patch_channel(cat_id, is_public=False, name=str(ch.get("name") or ""))

    # 2) Ajuster chaque salon
    for ch in channels:
        channel_id = str(ch["id"])
        if int(ch.get("type", 0)) not in GATE_CHANNEL_TYPES:
            continue
        _patch_channel(
            channel_id,
            channel_id in public_ids,
            name=str(ch.get("name") or ""),
        )

    return stats
