"""Panneau + tickets aide / support Discord."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from vinted_bot.interactions.recruitment_panel import (
    build_ticket_overwrites,
    format_ticket_transcript,
)

EMBED_COLOR = 0x57F287  # vert support
SUPPORT_OPEN = "support:open"
SUPPORT_CLOSE = "support:close"
TICKET_TOPIC_PREFIX = "aide:"
PANEL_TITLE = "🆘 Besoin d'aide ?"


def sanitize_support_channel_name(username: str) -> str:
    raw = (username or "membre").strip().lower()
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", folded).strip("-")
    if not cleaned:
        cleaned = "membre"
    return f"aide-{cleaned}"[:100]


def ticket_topic_for_user(user_id: int | str) -> str:
    return f"{TICKET_TOPIC_PREFIX}{int(user_id)}"


def parse_ticket_opener_id(topic: str | None) -> str:
    text = (topic or "").strip()
    if not text.startswith(TICKET_TOPIC_PREFIX):
        return ""
    return text[len(TICKET_TOPIC_PREFIX) :].strip()


def build_support_panel_payload() -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": PANEL_TITLE,
        "description": (
            "Un souci, une question, un problème sur Resello ?\n\n"
            "Clique sur le bouton pour ouvrir un **ticket privé** avec le staff.\n"
            "Décris clairement ton problème — on te répond au plus vite.\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "Exemples :\n"
            "• Accès / rôles / abonnement\n"
            "• Bug bot ou salon\n"
            "• Question sur une fonctionnalité\n\n"
            "_Un seul ticket aide ouvert à la fois._"
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Support"},
    }
    return {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": "📩 Ouvrir un ticket",
                        "custom_id": SUPPORT_OPEN,
                    }
                ],
            }
        ],
    }


def build_support_ticket_payload(
    *,
    opener_mention: str,
    staff_mention: str = "",
) -> dict[str, Any]:
    ping = f"{staff_mention}\n" if staff_mention else ""
    embed: dict[str, Any] = {
        "title": "🆘 Ticket aide Resello",
        "description": (
            f"{ping}"
            f"Salut {opener_mention} — ton ticket est ouvert.\n\n"
            "Explique ton problème **ici**, en précisant :\n\n"
            "1️⃣ **De quoi as-tu besoin ?**\n"
            "2️⃣ **Depuis quand ?**\n"
            "3️⃣ **Captures / liens** *(si utile)*\n\n"
            "━━━━━━━━━━━━━━\n"
            "Un membre du staff va te répondre.\n"
            "Quand c’est réglé, clique sur **Fermer**."
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Support"},
    }
    return {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 4,
                        "label": "🔒 Fermer",
                        "custom_id": SUPPORT_CLOSE,
                    }
                ],
            }
        ],
    }


def find_open_support_ticket(
    channels: list[dict[str, Any]],
    *,
    category_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    topic_want = ticket_topic_for_user(user_id)
    matches: list[dict[str, Any]] = []
    for ch in channels:
        if int(ch.get("type", -1)) != 0:
            continue
        topic = str(ch.get("topic") or "")
        if parse_ticket_opener_id(topic) == str(user_id) or topic == topic_want:
            matches.append(ch)
    if not matches:
        return None
    if category_id:
        for ch in matches:
            if str(ch.get("parent_id") or "") == str(category_id):
                return ch
    return matches[0]


__all__ = [
    "SUPPORT_OPEN",
    "SUPPORT_CLOSE",
    "PANEL_TITLE",
    "TICKET_TOPIC_PREFIX",
    "sanitize_support_channel_name",
    "ticket_topic_for_user",
    "parse_ticket_opener_id",
    "build_ticket_overwrites",
    "build_support_panel_payload",
    "build_support_ticket_payload",
    "format_ticket_transcript",
    "find_open_support_ticket",
]
