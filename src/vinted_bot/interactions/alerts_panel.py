"""Panneau salon #mes-alertes — filtres privés."""

from __future__ import annotations

from typing import Any, Sequence

ALERT_CREATE = "alert:create"
ALERT_LIST = "alert:list"
ALERT_CREATE_MODAL = "alert:create_modal"
ALERT_EDIT_SELECT = "alert:edit_select"
ALERT_PAUSE_SELECT = "alert:pause_select"
ALERT_DELETE_SELECT = "alert:delete_select"
ALERT_EDIT_MODAL_PREFIX = "alert:edit_modal:"

EMBED_COLOR = 0x5865F2

_NUM_EMOJI = (
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "4️⃣",
    "5️⃣",
    "6️⃣",
    "7️⃣",
    "8️⃣",
    "9️⃣",
    "🔟",
)


def _filter_title(row: Any) -> str:
    if getattr(row, "name", None) and str(row.name).strip():
        return str(row.name).strip()
    bits = [x for x in (getattr(row, "brand", None), getattr(row, "model", None)) if x]
    if bits:
        return " ".join(str(x) for x in bits)
    if getattr(row, "keyword", None):
        return str(row.keyword)
    return f"Filtre #{row.id}"


def _filter_criteria_lines(row: Any) -> list[str]:
    lines: list[str] = []
    if row.brand:
        lines.append(f"🏷️ Marque : {row.brand}")
    if row.model:
        lines.append(f"🔎 Modèle : {row.model}")
    if row.category:
        lines.append(f"📂 Catégorie : {row.category}")
    if row.keyword:
        lines.append(f"💬 Mot-clé : {row.keyword}")
    if row.min_price_eur is not None:
        lines.append(f"💰 Prix min : {float(row.min_price_eur):.0f} €")
    if row.max_price_eur is not None:
        lines.append(f"💰 Prix max : {float(row.max_price_eur):.0f} €")
    return lines


def build_mes_alertes_panel_payload() -> dict[str, Any]:
    """Message permanent du salon Mes alertes personnalisées."""
    embed: dict[str, Any] = {
        "title": "🔔 MES ALERTES PERSONNALISÉES",
        "description": (
            "Crée tes propres recherches Vinted.\n\n"
            "Le bot surveille le marché **24/7** et t'envoie les opportunités "
            "en **message privé** (DM Resello).\n\n"
            "Dans le DM : **activer / désactiver** uniquement.\n"
            "Ici : **créer · modifier · supprimer** tes filtres.\n\n"
            "**Exemples :**\n"
            "👟 Nike TN < 50 €\n"
            "🧥 Arc'teryx < 150 €\n"
            "👕 Ralph Lauren Vintage\n"
            "🧸 Objets de collection\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "⚙️ **Gérer tes alertes**"
        ),
        "color": EMBED_COLOR,
        "footer": {
            "text": "Privé · personne d'autre ne voit tes filtres ni tes alertes"
        },
    }
    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "➕ CRÉER UNE ALERTE",
                    "custom_id": ALERT_CREATE,
                },
                {
                    "type": 2,
                    "style": 1,
                    "label": "📋 MES ALERTES",
                    "custom_id": ALERT_LIST,
                },
            ],
        },
    ]
    return {"embeds": [embed], "components": components}


def build_user_alerts_payload(
    *,
    plan: str,
    filters: Sequence[Any],
    limit: int | None,
) -> dict[str, Any]:
    """Réponse éphémère après clic sur MES ALERTES / `/filtres`."""
    limit_txt = "∞" if limit is None else str(limit)
    plan_label = (plan or "starter").strip().upper()
    used = len(filters)

    parts: list[str] = [
        f"**Plan :** {plan_label}",
        f"**Filtres utilisés :** {used}/{limit_txt}",
        "",
        "🔒 Tes filtres sont privés.",
        "📩 Les opportunités seront envoyées uniquement en DM.",
        "",
        "━━━━━━━━━━━━━━",
    ]

    if not filters:
        parts.extend(
            [
                "",
                "_Aucune alerte pour l’instant._",
                "Clique **➕ Créer une alerte** pour commencer.",
                "",
                "━━━━━━━━━━━━━━",
            ]
        )
    else:
        for idx, row in enumerate(filters):
            num = _NUM_EMOJI[idx] if idx < len(_NUM_EMOJI) else f"**{idx + 1}.**"
            title = _filter_title(row)
            criteria = _filter_criteria_lines(row)
            status = "🟢 Actif" if row.is_active else "⏸️ En pause"
            parts.append("")
            parts.append(f"{num} 👟 **{title}**")
            parts.append("")
            if criteria:
                parts.extend(criteria)
            else:
                parts.append("_Aucun critère_")
            parts.append("")
            parts.append(status)
            parts.append("")
            parts.append("━━━━━━━━━━━━━━")

    parts.extend(["", "**Actions :**"])

    description = "\n".join(parts)
    # Discord embed description max 4096
    if len(description) > 4000:
        description = description[:3990] + "\n…"

    embed: dict[str, Any] = {
        "title": "🔔 TES ALERTES PERSONNALISÉES",
        "description": description,
        "color": EMBED_COLOR,
        "footer": {"text": "Privé · Resello"},
    }

    components: list[dict[str, Any]] = []
    if filters:
        options = []
        for idx, row in enumerate(filters[:25], start=1):
            title = _filter_title(row)[:80]
            status = "Actif" if row.is_active else "Pause"
            options.append(
                {
                    "label": f"#{idx} · {title}"[:100],
                    "value": str(int(row.id)),
                    "description": f"{status}"[:100],
                }
            )

        components.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": ALERT_EDIT_SELECT,
                        "placeholder": "✏️ Modifier un filtre",
                        "min_values": 1,
                        "max_values": 1,
                        "options": options,
                    }
                ],
            }
        )
        components.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": ALERT_PAUSE_SELECT,
                        "placeholder": "⏸️ Mettre en pause / reprendre",
                        "min_values": 1,
                        "max_values": 1,
                        "options": options,
                    }
                ],
            }
        )
        components.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": ALERT_DELETE_SELECT,
                        "placeholder": "🗑️ Supprimer un filtre",
                        "min_values": 1,
                        "max_values": 1,
                        "options": options,
                    }
                ],
            }
        )

    components.append(
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "➕ Créer une alerte",
                    "custom_id": ALERT_CREATE,
                },
            ],
        }
    )

    return {"embeds": [embed], "components": components}


def build_create_alert_modal() -> dict[str, Any]:
    """Modal Discord — création d'un filtre privé."""
    return {
        "custom_id": ALERT_CREATE_MODAL,
        "title": "Créer une alerte privée",
        "components": _alert_modal_rows(),
    }


def build_edit_alert_modal(
    *,
    filter_id: int,
    row: Any,
    display_number: int | None = None,
) -> dict[str, Any]:
    """Modal Discord — édition d'un filtre existant."""
    num = display_number if display_number is not None else int(filter_id)
    return {
        "custom_id": f"{ALERT_EDIT_MODAL_PREFIX}{int(filter_id)}",
        "title": f"Modifier filtre #{num}"[:45],
        "components": _alert_modal_rows(
            brand=row.brand or "",
            model=row.model or "",
            category=row.category or "",
            keyword=row.keyword or "",
            prix_max=(
                f"{float(row.max_price_eur):.0f}"
                if row.max_price_eur is not None
                else ""
            ),
        ),
    }


def _alert_modal_rows(
    *,
    brand: str = "",
    model: str = "",
    category: str = "",
    keyword: str = "",
    prix_max: str = "",
) -> list[dict[str, Any]]:
    def _field(
        custom_id: str,
        label: str,
        *,
        placeholder: str,
        value: str,
        max_length: int,
    ) -> dict[str, Any]:
        field: dict[str, Any] = {
            "type": 4,
            "custom_id": custom_id,
            "label": label,
            "style": 1,
            "placeholder": placeholder,
            "required": False,
            "max_length": max_length,
        }
        if value:
            field["value"] = value[:max_length]
        return field

    return [
        {
            "type": 1,
            "components": [
                _field(
                    "marque",
                    "Marque",
                    placeholder="Ex. Nike, Arc'teryx…",
                    value=brand,
                    max_length=80,
                )
            ],
        },
        {
            "type": 1,
            "components": [
                _field(
                    "modele",
                    "Modèle",
                    placeholder="Ex. TN, Alpha SV…",
                    value=model,
                    max_length=80,
                )
            ],
        },
        {
            "type": 1,
            "components": [
                _field(
                    "categorie",
                    "Catégorie",
                    placeholder="Ex. veste, hoodie…",
                    value=category,
                    max_length=80,
                )
            ],
        },
        {
            "type": 1,
            "components": [
                _field(
                    "mot_cle",
                    "Mot-clé",
                    placeholder="Ex. Jellycat, vintage…",
                    value=keyword,
                    max_length=100,
                )
            ],
        },
        {
            "type": 1,
            "components": [
                _field(
                    "prix_max",
                    "Prix maximum (€)",
                    placeholder="Ex. 50",
                    value=prix_max,
                    max_length=10,
                )
            ],
        },
    ]
