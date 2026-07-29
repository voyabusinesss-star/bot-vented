"""Panneau salon règlement — validation par bouton."""

from __future__ import annotations

from typing import Any

REGLEMENT_ACCEPT = "reglement:accept"

EMBED_COLOR = 0x5865F2


def build_reglement_panel_payload() -> dict[str, Any]:
    """Message permanent du salon règlement avec bouton d'acceptation."""
    embed: dict[str, Any] = {
        "title": "📜 Règlement Resello",
        "description": (
            "Bienvenue sur **Resello** — avant d'accéder au serveur, "
            "prends connaissance du règlement.\n\n"
            "**En rejoignant ce serveur, tu t'engages à :**\n"
            "• Respecter les autres membres et l'équipe\n"
            "• Ne pas spammer, harceler ou publier de contenu illégal\n"
            "• Utiliser les salons à bon escient (annonces, niches, alertes…)\n"
            "• Ne pas revendre ou diffuser le contenu premium sans autorisation\n"
            "• Signaler tout abus à l'équipe\n\n"
            "Le non-respect du règlement peut entraîner un mute, un kick "
            "ou un ban.\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "👇 **Clique ci-dessous pour confirmer que tu as lu et accepté "
            "le règlement.**"
        ),
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Accès au serveur"},
    }
    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "✅ J'accepte le règlement",
                    "custom_id": REGLEMENT_ACCEPT,
                }
            ],
        }
    ]
    return {"embeds": [embed], "components": components}
