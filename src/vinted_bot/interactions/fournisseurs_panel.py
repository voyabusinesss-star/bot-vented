"""Panneau fournisseur Fleek (#fournisseurs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

EMBED_COLOR = 0xF5D90A  # jaune Fleek
PANEL_TITLE = "🤝 Fleek — Fournisseur recommandé"
FLEEK_BANNER_FILENAME = "fleek-banner.png"
FLEEK_SITE_URL = "https://joinfleek.com"
FLEEK_PROMO_CODE = "RFD-U7EOTDD2"


def resolve_fleek_banner_path(raw: str = "") -> Path:
    candidate = Path((raw or "config/fleek-banner.png").strip())
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate


def build_fleek_panel_payload(*, banner_filename: str = FLEEK_BANNER_FILENAME) -> dict[str, Any]:
    description = (
        f"🔗 **Site officiel :** [{FLEEK_SITE_URL.replace('https://', '')}]({FLEEK_SITE_URL})\n\n"
        "Fleek est une marketplace spécialisée qui met en relation les revendeurs "
        "avec plus de **1 000 fournisseurs vintage vérifiés** à travers le monde. "
        "Tu peux y acheter des **lots** ou des **pièces à l'unité** avec une "
        "protection acheteur intégrée.\n\n"
        "✅ **Pourquoi Fleek ?**\n"
        "🌍 Grossistes vérifiés\n"
        "📦 Lots & pièces à l'unité\n"
        "👕 Marques premium (Nike, Carhartt, Lacoste, Ralph Lauren…)\n"
        "🚚 Livraison en France\n"
        "🛡️ Protection acheteur\n\n"
        "🎁 **Avantage exclusif Resello**\n"
        "💸 **-30 €** sur ta première commande\n\n"
        "━━━━━━━━━━━━━━\n"
        f"# **{FLEEK_PROMO_CODE}**\n"
        "━━━━━━━━━━━━━━\n\n"
        "Réserve ce code à ta **première commande** pour profiter de "
        "**30 € de réduction**."
    )
    return {
        "embeds": [
            {
                "title": PANEL_TITLE,
                "description": description[:4096],
                "color": EMBED_COLOR,
                "fields": [
                    {
                        "name": "🏷️ CODE PROMO",
                        "value": f"**```{FLEEK_PROMO_CODE}```**",
                        "inline": False,
                    }
                ],
                "image": {"url": f"attachment://{banner_filename}"},
                "footer": {"text": "Resello · Fournisseurs"},
            }
        ],
    }
