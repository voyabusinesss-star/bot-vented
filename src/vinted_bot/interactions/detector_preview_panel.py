"""Panneau salon — aperçu marketing du détecteur de niches."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from vinted_bot.services.market_embeds import COLOR_GREEN, score_stars
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2
PREVIEW_NICHE_KEY = "wrangler||chemise|"
EXPLORE_URL = (
    "https://www.vinted.fr/catalog?"
    + urlencode([("search_text", "wrangler Chemise noir"), ("order", "newest_first")])
)


def _fallback_example_embed(*, photo_url: str | None = None) -> dict[str, Any]:
    """Exemple statique si la niche Wrangler n'est pas en base."""
    score = 68.0
    embed: dict[str, Any] = {
        "title": "🧠 Wrangler Chemise noir"[:256],
        "url": EXPLORE_URL,
        "color": COLOR_GREEN,
        "description": (
            "**Wrangler Chemise noir**\n\n"
            f"⭐ Opportunité : **{score:.0f}/100**\n"
            f"{score_stars(score)}\n\n"
            "🔥 Forte demande\n"
            "📈 En croissance\n"
            "💎 Opportunité intéressante\n"
            "📈 Croissance\n\n"
            f"🔗 [Voir les annonces similaires sur Vinted]({EXPLORE_URL})"
        )[:3900],
        "fields": [
            {
                "name": "💰 Marché",
                "value": "Achat observé :\n**18 €**\n\nRevente moyenne :\n**35 €**",
                "inline": True,
            },
            {
                "name": "📊 Signaux marché",
                "value": "🔥 Demande : **+47 %**\n⚡ Vente : **+5 %**",
                "inline": True,
            },
            {
                "name": "🧠 Pourquoi ?",
                "value": (
                    "flux récent (17 annonces) + écart prix ~94% + signal émergent · "
                    "demande en hausse\n"
                    "Signal principal : demande en hausse."
                ),
                "inline": False,
            },
            {
                "name": "🔎 Rechercher",
                "value": (
                    "**Mots-clés :**\n"
                    "• Wrangler\n"
                    "• Chemise\n"
                    "• noir\n\n"
                    "**Variantes intéressantes :**\n"
                    "• couleurs noir/blanc/beige\n"
                    "• tailles L,M,S,XL,XXL"
                ),
                "inline": False,
            },
            {
                "name": "🔗 Explorer la niche",
                "value": (
                    f"[Ouvrir le catalogue « wrangler Chemise noir »]({EXPLORE_URL})\n"
                    f"`{EXPLORE_URL}`"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "📌 Exemple d'analyse détecteur · 17 annonces analysées"},
    }
    if photo_url:
        embed["image"] = {"url": photo_url}
    return embed


def _example_embed_from_db() -> dict[str, Any] | None:
    """Construit l'exemple à partir de la niche Wrangler en base."""
    try:
        from vinted_bot.services.niche_product_sheets import _snapshot_for_key
        from vinted_bot.services.opportunity_engine import (
            build_opportunity_embed,
            snapshot_to_opportunity,
        )

        snap = _snapshot_for_key(PREVIEW_NICHE_KEY)
        if snap is None:
            return None
        op = snapshot_to_opportunity(snap)
        if op is None:
            return None
        return build_opportunity_embed(op, listings_analyzed=op.listing_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("detector_preview_example_failed", error=str(exc)[:160])
        return None


def _preview_photo_url() -> str | None:
    """Photo produit : chemise Wrangler noire (priorité titre noir/black)."""
    try:
        from sqlalchemy import or_, select
        from sqlalchemy.orm import selectinload

        from vinted_bot.db.models import Listing
        from vinted_bot.db.session import session_scope
        from vinted_bot.notify.discord import _photo_urls

        base = (
            select(Listing)
            .options(selectinload(Listing.photos))
            .where(Listing.is_active.is_(True))
            .where(Listing.title.ilike("%wrangler%"))
            .where(or_(Listing.title.ilike("%chemis%"), Listing.title.ilike("%shirt%")))
            .order_by(Listing.last_seen_at.desc().nullslast())
            .limit(40)
        )
        with session_scope() as session:
            rows = list(session.scalars(base).unique().all())
            noir_rows = [
                r
                for r in rows
                if any(
                    tok in (r.title or "").lower()
                    for tok in ("noir", "noire", "black", "marine")
                )
            ]
            for row in noir_rows + [r for r in rows if r not in noir_rows]:
                photos = _photo_urls(row)
                if photos:
                    return photos[0]
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("detector_preview_photo_failed", error=str(exc)[:120])
        return None


def _apply_product_photo(embed: dict[str, Any]) -> dict[str, Any]:
    photo = _preview_photo_url()
    if not photo:
        return embed
    return {**embed, "image": {"url": photo}}


def build_detector_preview_panel_payload() -> dict[str, Any]:
    """Message permanent : intro + exemple réel (Wrangler Chemise noir)."""
    intro: dict[str, Any] = {
        "title": "🧠 APERÇU DÉTECTEUR DE NICHES",
        "description": (
            "Découvre comment **Resello** détecte automatiquement les meilleures "
            "opportunités du marché grâce à l'analyse continue de milliers d'annonces.\n\n"
            "⬇️ **Exemple réel d'une analyse Premium**\n\n"
            "📸 _Analyse exemple ci-dessous — même format que le détecteur live_\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "🔒 Les analyses complètes sont réservées aux membres **Premium**."
        ),
        "color": EMBED_COLOR,
        "footer": {"text": "Détecteur de niches · Resello"},
    }

    example = _example_embed_from_db()
    if example is None:
        example = _fallback_example_embed(photo_url=_preview_photo_url())
    else:
        example = _apply_product_photo(example)

    return {"embeds": [intro, example]}
