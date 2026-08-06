"""Verrouillage des salons jusqu'à acceptation du règlement.

Modèle d'accès :
- @everyone : salons publics (bienvenue / règlement / présentation)
- Membre (règlement OK, pas encore Whop) : Accueil + Rejoindre + Support + Avant-goût
- Starter / Pro / Pro+ (Whop) : salons marques, communauté, guides, etc.
"""

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

# IDs connus Resello (surchargeables via .env)
_DEFAULT_PREVIEW_CATEGORIES = {
    "discord_category_accueil": "1529222602508337265",
    "discord_category_rejoindre": "1532290924028231690",
    "discord_category_support": "1530565834244755527",
    "discord_category_avant_gout": "1531568468061716490",
}

_PREVIEW_CATEGORY_NAME_HINTS = (
    ("accueil", "discord_category_accueil"),
    ("rejoindre", "discord_category_rejoindre"),
    ("support", "discord_category_support"),
    ("avant-goût", "discord_category_avant_gout"),
    ("avant-gout", "discord_category_avant_gout"),
    ("avant goût", "discord_category_avant_gout"),
)


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
    """Fusionne VIEW_CHANNEL sans écraser les autres bits d'overwrite."""
    existing_ow: dict[str, Any] | None = None
    result: list[dict[str, Any]] = []
    for ow in overwrites:
        if str(ow.get("id")) == target_id and int(ow.get("type", 0)) == target_type:
            existing_ow = dict(ow)
            continue
        result.append(dict(ow))

    allow_i = _parse_perm(existing_ow.get("allow") if existing_ow else 0)
    deny_i = _parse_perm(existing_ow.get("deny") if existing_ow else 0)
    if allow_view:
        allow_i |= VIEW_CHANNEL
        deny_i &= ~VIEW_CHANNEL
    else:
        deny_i |= VIEW_CHANNEL
        allow_i &= ~VIEW_CHANNEL

    result.append(
        {
            "id": target_id,
            "type": target_type,
            "allow": str(allow_i),
            "deny": str(deny_i),
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
    deny_member: bool = False,
) -> list[dict[str, Any]]:
    """Salon public, aperçu Membre, ou interdit à Membre (abo Whop / privé)."""
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
    if deny_member:
        overwrites = merge_view_channel_overwrite(
            overwrites,
            target_id=member_role_id,
            target_type=0,
            allow_view=False,
        )
        overwrites = _allow_view_for_roles(overwrites, keep_bot)
        return overwrites
    overwrites = _allow_view_for_roles(overwrites, [member_role_id, *keep_bot])
    return overwrites


def find_role_by_name(roles: list[dict[str, Any]], name: str) -> str:
    target = name.strip().lower()
    for role in roles:
        if str(role.get("name") or "").strip().lower() == target:
            return str(role["id"])
    return ""


def ensure_membre_role(client: Any, guild_id: str) -> str:
    """Retourne l'ID du rôle Membre (existant ou créé).

    Ne modifie pas les permissions serveur du rôle (laissées à Discord /
    config manuelle). L'accès aux salons payants se gère via overwrites.
    """
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
            "permissions": str(VIEW_CHANNEL),
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
    """Salons visibles avant validation du règlement."""
    public: set[str] = set()
    for field in (
        "discord_channel_bienvenue",
        "discord_channel_reglement",
        "discord_channel_presentation",
    ):
        cid = sanitize_discord_channel_id(getattr(settings, field, "") or "")
        if cid:
            public.add(cid)
    return public


def resolve_preview_category_ids(
    settings: Any,
    channels: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Catégories aperçu Membre (Accueil, Rejoindre, Support, Avant-goût)."""
    found: dict[str, str] = {}
    for field, default in _DEFAULT_PREVIEW_CATEGORIES.items():
        cid = sanitize_discord_channel_id(getattr(settings, field, "") or "") or default
        if cid:
            found[field] = cid

    if channels:
        for ch in channels:
            if int(ch.get("type", 0)) != CATEGORY_TYPE:
                continue
            name = str(ch.get("name") or "").casefold()
            for hint, field in _PREVIEW_CATEGORY_NAME_HINTS:
                if hint in name and field not in found:
                    found[field] = str(ch["id"])

    return {cid for cid in found.values() if cid}


def resolve_membre_preview_channel_ids(
    settings: Any,
    channels: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Salons + catégories visibles avec Membre (sans abonnement Whop)."""
    from vinted_bot.services.discord_role_perms import channel_ids_in_category

    preview: set[str] = set()
    for cat_id in resolve_preview_category_ids(settings, channels):
        if channels is not None:
            preview |= channel_ids_in_category(channels, cat_id)
        else:
            preview.add(cat_id)

    # Salons Accueil souvent listés explicitement
    for field in (
        "discord_channel_annonces",
        "discord_channel_concours",
        "discord_channel_support",
        "discord_channel_recruitment",
        "discord_channel_subscriptions",
        "discord_channel_bot_preview",
        "discord_channel_niches_demo",
    ):
        cid = sanitize_discord_channel_id(getattr(settings, field, "") or "")
        if cid:
            preview.add(cid)
    return preview


def resolve_membre_denied_channel_ids(
    settings: Any,
    channels: list[dict[str, Any]] | None = None,
    *,
    public_channel_ids: set[str] | None = None,
    preview_channel_ids: set[str] | None = None,
) -> set[str]:
    """Tout sauf public + aperçu Membre → interdit au rôle Membre."""
    public_ids = public_channel_ids or resolve_public_channel_ids(settings)
    preview_ids = preview_channel_ids or resolve_membre_preview_channel_ids(
        settings, channels
    )
    if channels is None:
        denied: set[str] = set()
        private_cat = sanitize_discord_channel_id(
            getattr(settings, "discord_category_private_tools", "") or ""
        )
        if private_cat:
            denied.add(private_cat)
        for field in (
            "discord_channel_mes_alertes",
            "discord_channel_logs",
            "discord_channel_catalog_host",
        ):
            cid = sanitize_discord_channel_id(getattr(settings, field, "") or "")
            if cid:
                denied.add(cid)
        return denied - public_ids - preview_ids

    denied = set()
    for ch in channels:
        cid = str(ch.get("id") or "")
        if not cid:
            continue
        ctype = int(ch.get("type", 0))
        if ctype not in GATE_CHANNEL_TYPES and ctype != CATEGORY_TYPE:
            continue
        if cid in public_ids or cid in preview_ids:
            continue
        denied.add(cid)
    return denied


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
    preview_channel_ids: set[str] | None = None,
    denied_channel_ids: set[str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Applique public / aperçu Membre / reste interdit Membre."""
    from vinted_bot.config import get_settings

    everyone_id = guild_id
    bot_role_ids = find_bot_role_ids(client, guild_id)
    response = client._client.get(f"/guilds/{guild_id}/channels")
    if response.status_code >= 400:
        raise RuntimeError(
            f"List channels {response.status_code}: {response.text[:400]}"
        )

    stats = {
        "updated": 0,
        "skipped": 0,
        "public": 0,
        "gated": 0,
        "denied": 0,
        "failed": 0,
    }
    channels = response.json()
    settings = get_settings()
    public_ids = {str(ch_id) for ch_id in public_channel_ids}
    preview_ids = {
        str(ch_id)
        for ch_id in (
            preview_channel_ids
            if preview_channel_ids is not None
            else resolve_membre_preview_channel_ids(settings, channels)
        )
        if str(ch_id)
    }
    preview_ids |= public_ids
    denied_ids = resolve_membre_denied_channel_ids(
        settings,
        channels,
        public_channel_ids=public_ids,
        preview_channel_ids=preview_ids,
    )
    if denied_channel_ids is not None:
        denied_ids |= {str(ch_id) for ch_id in denied_channel_ids if str(ch_id)}
    denied_ids -= public_ids
    denied_ids -= preview_ids

    def _patch_channel(
        channel_id: str,
        *,
        is_public: bool,
        deny_member: bool,
        name: str = "",
    ) -> None:
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
            deny_member=deny_member,
        )
        if dry_run:
            stats["updated"] += 1
            if is_public:
                stats["public"] += 1
            elif deny_member:
                stats["denied"] += 1
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
        elif deny_member:
            stats["denied"] += 1
        else:
            stats["gated"] += 1
        log.info(
            "reglement_gate_applied",
            channel_id=channel_id,
            name=name,
            public=is_public,
            deny_member=deny_member,
        )

    # 1) Catégories (sauf Accueil qui contient des salons publics)
    for ch in channels:
        if int(ch.get("type", 0)) != CATEGORY_TYPE:
            continue
        cat_id = str(ch["id"])
        children = [c for c in channels if str(c.get("parent_id") or "") == cat_id]
        if any(str(c["id"]) in public_ids for c in children):
            continue
        is_preview = cat_id in preview_ids
        _patch_channel(
            cat_id,
            is_public=False,
            deny_member=not is_preview,
            name=str(ch.get("name") or ""),
        )

    # 2) Salons
    for ch in channels:
        channel_id = str(ch["id"])
        if int(ch.get("type", 0)) not in GATE_CHANNEL_TYPES:
            continue
        is_public = channel_id in public_ids
        is_preview = channel_id in preview_ids
        _patch_channel(
            channel_id,
            is_public=is_public,
            deny_member=(not is_public) and (not is_preview),
            name=str(ch.get("name") or ""),
        )

    return stats
