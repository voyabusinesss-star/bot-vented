"""Activation abo Whop depuis Discord (sans lier Discord sur Whop)."""

from __future__ import annotations

from typing import Any

WHOP_CLAIM = "whop:claim"
WHOP_CLAIM_MODAL = "whop:claim_modal"
WHOP_CLAIM_FIELD = "whop_claim_ref"
WHOP_CHECKOUT_PREFIX = "whop:checkout:"

EMBED_COLOR = 0x5865F2


def build_whop_claim_components() -> list[dict[str, Any]]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "✅ Activer mon accès",
                    "custom_id": WHOP_CLAIM,
                },
            ],
        }
    ]


def build_whop_claim_panel_payload() -> dict[str, Any]:
    """Panneau à coller dans Nos offres / Rejoindre."""
    return {
        "embeds": [
            {
                "title": "✅ Activer mon abonnement Resello",
                "description": (
                    "Tu as rejoint via Whop **sans** connecter Discord ? "
                    "Pas de souci.\n\n"
                    "1. Paie / rejoins sur Whop\n"
                    "2. Clique **Activer mon accès**\n"
                    "3. Entre l’**email** utilisé sur Whop "
                    "*(ou ton ID membership `mem_…`)*\n\n"
                    "Le bot te donne le rôle **Starter / Pro / Pro+** "
                    "automatiquement."
                ),
                "color": EMBED_COLOR,
                "footer": {"text": "Resello · Whop"},
            }
        ],
        "components": build_whop_claim_components(),
    }


def build_whop_claim_modal() -> dict[str, Any]:
    return {
        "custom_id": WHOP_CLAIM_MODAL,
        "title": "Activer mon abo Whop",
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 4,
                        "custom_id": WHOP_CLAIM_FIELD,
                        "label": "Email Whop ou mem_…",
                        "style": 1,
                        "min_length": 3,
                        "max_length": 200,
                        "placeholder": "toi@email.com ou mem_xxxxx",
                        "required": True,
                    }
                ],
            }
        ],
    }


def build_subscriptions_claim_row() -> dict[str, Any]:
    """Rangée de boutons pour l'intro Nos offres."""
    return {
        "type": 1,
        "components": [
            {
                "type": 2,
                "style": 3,
                "label": "✅ Activer mon accès",
                "custom_id": WHOP_CLAIM,
            },
            {
                "type": 2,
                "style": 1,
                "label": "🟢 Lien Starter",
                "custom_id": f"{WHOP_CHECKOUT_PREFIX}starter",
            },
            {
                "type": 2,
                "style": 1,
                "label": "🔵 Lien Pro",
                "custom_id": f"{WHOP_CHECKOUT_PREFIX}pro",
            },
            {
                "type": 2,
                "style": 1,
                "label": "🟣 Lien Pro+",
                "custom_id": f"{WHOP_CHECKOUT_PREFIX}proplus",
            },
        ],
    }
