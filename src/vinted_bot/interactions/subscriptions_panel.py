"""Publication des offres d'abonnement Resello (texte + bannière en bas)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

IMAGE_MIME = "image/png"
SUBSCRIPTIONS_DIR = "config/subscriptions"
EMBED_COLOR = 0x5865F2


@dataclass(frozen=True, slots=True)
class SubscriptionTier:
    key: str
    banner_file: str
    title: str
    hook: str
    features: tuple[str, ...]
    quote: str
    price: str
    color: int


SUBSCRIPTION_TIERS: tuple[SubscriptionTier, ...] = (
    SubscriptionTier(
        key="starter",
        banner_file="resello-starter-banner.png",
        title="ABONNEMENT STARTER",
        hook="👉 Idéal pour **commencer** l'achat/revente **efficacement**",
        features=(
            "• **Accès** au serveur premium Resello",
            "• **Bot scraping** Vinted 0 délai",
            "• **Filtres classiques** (salons marques)",
            "• **Guides** achat/revente",
            "• **Détecteur** de niches",
            "• **Accès** aux salons premium",
            "• ❌ **Aucun filtre privé** inclus",
        ),
        quote="Parfait pour découvrir l'écosystème avant de passer au niveau supérieur.",
        price="14,99 € / mois",
        color=0x2ECC71,
    ),
    SubscriptionTier(
        key="pro",
        banner_file="resello-pro-banner.png",
        title="ABONNEMENT PRO",
        hook="👉 L'offre **recommandée** pour les revendeurs **actifs**",
        features=(
            "• **Tout le Starter**",
            "• **Accès** aux filtres privés Resello",
            "• **10 filtres privés** / mois",
            "• **Filtres avancés** exclusifs",
            "• **Accès** aux nouveautés en avant-première",
        ),
        quote="L'équilibre idéal entre prix et puissance pour scaler sérieusement.",
        price="19,99 € / mois",
        color=0x3498DB,
    ),
    SubscriptionTier(
        key="proplus",
        banner_file="resello-proplus-banner.png",
        title="ABONNEMENT PRO+",
        hook="👉 Pour **maximiser** tes opportunités et rester **en avance**",
        features=(
            "• **Tout le Pro**",
            "• **30 filtres privés** / mois",
            "• **Priorité** sur les demandes de filtres",
            "• **Filtres plus complexes**",
            "• **Accès prioritaire** aux nouvelles fonctionnalités",
        ),
        quote="Pour les revendeurs qui veulent le maximum de couverture marché.",
        price="24,99 € / mois",
        color=0x9B59B6,
    ),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_subscriptions_dir(raw: str | None = None) -> Path:
    settings = get_settings()
    text = (
        raw
        or getattr(settings, "subscriptions_images_path", "")
        or SUBSCRIPTIONS_DIR
    ).strip()
    path = Path(text)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def _checkout_url(tier: SubscriptionTier) -> str | None:
    from vinted_bot.services.whop_webhooks import resolve_whop_checkout_url

    url = resolve_whop_checkout_url(tier.key)
    return url or None


def build_subscription_embed_payload(
    tier: SubscriptionTier,
    *,
    banner_filename: str,
    checkout_url: str | None = None,
) -> dict[str, Any]:
    """Texte en haut, bannière en image en bas (attachment://)."""
    lines = [
        tier.hook,
        "",
        *tier.features,
        "",
        f"> {tier.quote}",
        "",
        f"💳 **{tier.price}**",
        "",
        "👆 Utilise le bouton **Lien …** en haut du salon "
        "pour un accès **automatique**.",
    ]

    embed: dict[str, Any] = {
        "title": tier.title,
        "description": "\n".join(lines)[:4096],
        "color": tier.color,
        "image": {"url": f"attachment://{banner_filename}"},
        "footer": {"text": "Resello · Abonnements"},
    }
    return {"embeds": [embed]}


def build_subscriptions_intro_payload() -> dict[str, Any]:
    """Message d'en-tête en haut du salon Nos offres."""
    from vinted_bot.interactions.whop_claim_panel import build_subscriptions_claim_row

    by_key = {t.key: t for t in SUBSCRIPTION_TIERS}

    def _short(tier_key: str) -> str:
        return by_key[tier_key].price.replace(" € / mois", "€/mois")

    embed: dict[str, Any] = {
        "title": "💎 Nos offres Resello 🚀",
        "description": (
            "Choisis l'offre adaptée à ton niveau et accède aux outils "
            "pour améliorer ton resell Vinted.\n\n"
            f"🟢 **Starter — {_short('starter')}**\n"
            f"🔵 **Pro — {_short('pro')} ⭐**\n"
            f"🟣 **Pro+ — {_short('proplus')}**\n\n"
            "**Important :** clique un bouton **Lien …** ci-dessous "
            "pour rejoindre. Le paiement est lié à ton Discord → "
            "le rôle est attribué **automatiquement** (sans admin).\n\n"
            "Si tu as payé via un autre lien : **Activer mon accès**."
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Abonnements"},
    }
    return {"embeds": [embed], "components": [build_subscriptions_claim_row()]}


def load_banner_bytes(
    tier: SubscriptionTier,
    *,
    base_dir: str | None = None,
) -> tuple[str, bytes]:
    root = resolve_subscriptions_dir(base_dir)
    path = root / tier.banner_file
    if not path.is_file():
        raise FileNotFoundError(
            f"Bannière abonnement introuvable : {path}\n"
            f"Place {tier.banner_file} dans {root}/"
        )
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Bannière abonnement vide : {path}")
    return tier.banner_file, data


def purge_subscriptions_channel(client: Any, channel_id: str) -> int:
    """Supprime les anciens messages abonnements du bot dans le salon."""
    from vinted_bot.config import discord_application_id

    token = getattr(client.settings, "discord_bot_token", "") or ""
    bot_id = discord_application_id(token)
    if not bot_id:
        return 0
    deleted = 0
    try:
        response = client._client.get(
            f"/channels/{channel_id}/messages",
            params={"limit": 50},
        )
        if response.status_code != 200:
            return 0
        for msg in response.json():
            author = msg.get("author") or {}
            if str(author.get("id")) != bot_id:
                continue
            try:
                client.delete_channel_message(channel_id, str(msg["id"]))
                deleted += 1
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.debug("subscriptions_purge_failed", error=str(exc)[:120])
    return deleted


def post_subscriptions_messages(
    client: Any,
    *,
    channel_id: str,
    guild_id: str,
    webhook_url: str | None = None,
    images_dir: str | None = None,
    purge: bool = True,
) -> list[dict[str, Any]]:
    """Publie 3 messages : embed texte + bannière en bas."""
    if purge:
        removed = purge_subscriptions_channel(client, channel_id)
        if removed:
            log.info("subscriptions_channel_purged", channel_id=channel_id, removed=removed)

    posted: list[dict[str, Any]] = []

    intro = client.post_channel_payload_as_guild_with_attachments(
        channel_id,
        build_subscriptions_intro_payload(),
        guild_id=guild_id,
        webhook_url=webhook_url,
        attachments=None,
    )
    posted.append(intro)
    log.info(
        "subscriptions_intro_posted",
        channel_id=channel_id,
        message_id=intro.get("id"),
    )

    for tier in SUBSCRIPTION_TIERS:
        banner_name, banner_bytes = load_banner_bytes(tier, base_dir=images_dir)
        payload = build_subscription_embed_payload(
            tier,
            banner_filename=banner_name,
            checkout_url=_checkout_url(tier),
        )
        message = client.post_channel_payload_as_guild_with_attachments(
            channel_id,
            payload,
            guild_id=guild_id,
            webhook_url=webhook_url,
            attachments=[(banner_name, banner_bytes, IMAGE_MIME)],
        )
        posted.append(message)
        log.info(
            "subscriptions_tier_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
            tier=tier.key,
            banner=banner_name,
        )

    log.info(
        "subscriptions_posted",
        channel_id=channel_id,
        messages=len(posted),
    )
    return posted
