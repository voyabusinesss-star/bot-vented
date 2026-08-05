"""Panneau salon règlement — validation par bouton."""

from __future__ import annotations

from typing import Any

REGLEMENT_ACCEPT = "reglement:accept"

EMBED_COLOR = 0x5865F2


def build_reglement_accept_components() -> list[dict[str, Any]]:
    return [
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


def build_reglement_panel_payload() -> dict[str, Any]:
    """Message permanent du salon règlement avec bouton d'acceptation."""
    embed: dict[str, Any] = {
        "description": (
            "**🤝 Respect**\n"
            "Zero insulte, propos discriminatoires ou harcèlement → sanction immédiate.\n"
            "Pas de clash public ; règle-le en DM ou avec un modérateur.\n\n"
            "**🛍️ Achat-revente**\n"
            "3. Zero arnaque → ban direct et définitif.\n"
            "4. Sois honnête sur l'état des articles (défauts, taille, usure).\n\n"
            "**🤖 Bot**\n"
            "5. Pas de spam/flood de commandes, pas d'exploitation du bot.\n\n"
            "**📢 Pub**\n"
            "6. Pas d'auto-promo sans accord mod, pas de liens suspects.\n\n"
            "**📁 Organisation**\n"
            "7. Un salon = un sujet. Pas de NSFW ni contenu hors ToS Discord.\n\n"
            "**⚖️ Sanctions**\n"
            "Avertissement → Mute → Kick → Ban "
            "(sauf arnaques/propos graves = ban direct).\n\n"
            "En restant ici, tu acceptes ce règlement. Bonnes trouvailles !\n\n"
            "**🚫 LEAKS INTERDITS**\n"
            "Toute diffusion, partage ou revente de contenus privés entraînera "
            "un bannissement immédiat et définitif."
        )[:4096],
        "color": EMBED_COLOR,
    }
    return {
        "embeds": [embed],
        "components": build_reglement_accept_components(),
    }


def _bot_user_id(client: Any) -> str:
    app_id = str(getattr(client, "application_id", "") or "").strip()
    if app_id:
        return app_id
    token = str(getattr(client.settings, "discord_bot_token", "") or "")
    if not token:
        return ""
    from vinted_bot.config import discord_application_id

    return discord_application_id(token)


def attach_reglement_button(
    client: Any,
    *,
    channel_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Ajoute le bouton sur un message existant (sans changer le texte)."""
    response = client._client.get(f"/channels/{channel_id}/messages/{message_id}")
    if response.status_code >= 400:
        raise RuntimeError(
            f"Fetch message {response.status_code}: {response.text[:400]}"
        )
    message = response.json()
    author_id = str((message.get("author") or {}).get("id") or "")
    bot_id = _bot_user_id(client)
    if author_id != bot_id:
        raise PermissionError(
            "Ce message a été publié par un autre bot — seul Resello peut "
            "ajouter un bouton interactif. Utilise post-reglement --replace."
        )

    body: dict[str, Any] = {
        "components": build_reglement_accept_components(),
    }
    if message.get("content"):
        body["content"] = message["content"]
    if message.get("embeds"):
        body["embeds"] = message["embeds"]

    edited = client.edit_channel_message(channel_id, message_id, body)
    return edited


def replace_reglement_panel(
    client: Any,
    *,
    channel_id: str,
    remove_message_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Supprime les anciens messages règlement et reposte un seul message Resello."""
    for mid in remove_message_ids or []:
        if not mid:
            continue
        try:
            client.delete_channel_message(channel_id, mid)
        except Exception:  # noqa: BLE001
            pass
    payload = build_reglement_panel_payload()
    return client.post_channel_payload(channel_id, payload)
