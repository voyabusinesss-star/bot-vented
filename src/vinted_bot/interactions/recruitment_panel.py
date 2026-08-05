"""Panneau + tickets recrutement Discord."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vinted_bot.services.reglement_gates import VIEW_CHANNEL, merge_view_channel_overwrite
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2
RECRUIT_OPEN = "recruit:open"
RECRUIT_CLOSE = "recruit:close"
TICKET_TOPIC_PREFIX = "recruit:"
PANEL_TITLE = "🚀 Recrutement staff Resello"

# Permissions Discord (bitfield)
# Important : Discord refuse (403) les overwrites qui ALLOW des perms
# que le bot n'a pas lui-même. Resello a VIEW + SEND (+ Manage Channels/Roles).
SEND_MESSAGES = 1 << 11
MANAGE_MESSAGES = 1 << 13
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
TICKET_MEMBER_ALLOW = VIEW_CHANNEL | SEND_MESSAGES
TICKET_STAFF_ALLOW = TICKET_MEMBER_ALLOW


def sanitize_ticket_channel_name(username: str) -> str:
    raw = (username or "membre").strip().lower()
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", folded).strip("-")
    if not cleaned:
        cleaned = "membre"
    name = f"recrutement-{cleaned}"
    return name[:100]


def ticket_topic_for_user(user_id: int | str) -> str:
    return f"{TICKET_TOPIC_PREFIX}{int(user_id)}"


def parse_ticket_opener_id(topic: str | None) -> str:
    text = (topic or "").strip()
    if not text.startswith(TICKET_TOPIC_PREFIX):
        return ""
    return text[len(TICKET_TOPIC_PREFIX) :].strip()


def _perm_overwrite(
    *,
    target_id: str,
    target_type: int,
    allow: int = 0,
    deny: int = 0,
) -> dict[str, Any]:
    return {
        "id": str(target_id),
        "type": int(target_type),
        "allow": str(int(allow)),
        "deny": str(int(deny)),
    }


def build_ticket_overwrites(
    *,
    everyone_id: str,
    opener_user_id: str,
    bot_user_id: str,
    staff_role_id: str = "",
    extra_role_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """@everyone deny view ; candidat / bot / staff allow."""
    overwrites = [
        _perm_overwrite(
            target_id=everyone_id,
            target_type=0,
            deny=VIEW_CHANNEL,
        ),
        _perm_overwrite(
            target_id=opener_user_id,
            target_type=1,
            allow=TICKET_MEMBER_ALLOW,
        ),
        _perm_overwrite(
            target_id=bot_user_id,
            target_type=1,
            allow=TICKET_STAFF_ALLOW,
        ),
    ]
    role_ids = []
    if staff_role_id:
        role_ids.append(staff_role_id)
    for rid in extra_role_ids or []:
        if rid and rid not in role_ids:
            role_ids.append(rid)
    for rid in role_ids:
        overwrites.append(
            _perm_overwrite(
                target_id=rid,
                target_type=0,
                allow=TICKET_STAFF_ALLOW,
            )
        )
    return overwrites


def build_recruitment_panel_payload() -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": PANEL_TITLE,
        "description": (
            "Resello grandit — on cherche des profils motivés pour renforcer le staff.\n\n"
            "**Modération · Community · Support**\n\n"
            "Clique sur le bouton pour ouvrir un **ticket privé**. "
            "Tu pourras y déposer ta candidature en quelques minutes.\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "Dans le ticket, on te demandera :\n"
            "• Ton âge\n"
            "• Le poste visé\n"
            "• Ton expérience staff\n"
            "• Ta motivation & tes qualités\n\n"
            "_Un seul ticket ouvert à la fois. Réponses claires = meilleure chance._"
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Recrutement"},
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
                        "custom_id": RECRUIT_OPEN,
                    }
                ],
            }
        ],
    }


def build_ticket_candidature_payload(
    *,
    opener_mention: str,
    staff_mention: str = "",
) -> dict[str, Any]:
    ping = f"{staff_mention}\n" if staff_mention else ""
    embed: dict[str, Any] = {
        "title": "🎯 Candidature staff Resello",
        "description": (
            f"{ping}"
            f"Bienvenue {opener_mention} — ton ticket de recrutement est ouvert.\n\n"
            "Réponds **directement dans ce salon**, point par point :\n\n"
            "1️⃣ **Âge**\n"
            "2️⃣ **Poste demandé** *(modération, community, support…)*\n"
            "3️⃣ **Expérience staff** *(si oui : où, combien de temps ?)*\n"
            "4️⃣ **Pourquoi Resello ?**\n"
            "5️⃣ **Tes 3 meilleures qualités**\n\n"
            "━━━━━━━━━━━━━━\n"
            "Sois clair et honnête — un membre du staff reviendra vers toi.\n"
            "Quand tu as terminé (ou pour abandonner), clique sur **Fermer**."
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Recrutement staff"},
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
                        "custom_id": RECRUIT_CLOSE,
                    }
                ],
            }
        ],
    }


def _message_author_label(msg: dict[str, Any]) -> str:
    author = msg.get("author") or {}
    name = (
        author.get("global_name")
        or author.get("username")
        or author.get("id")
        or "inconnu"
    )
    disc = author.get("discriminator")
    if disc and str(disc) not in {"0", "0000"}:
        return f"{name}#{disc}"
    return str(name)


def _message_timestamp_local(msg: dict[str, Any]) -> str:
    raw = msg.get("timestamp") or ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo("Europe/Paris"))
        return local.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(raw)[:16]


def format_ticket_transcript(messages: list[dict[str, Any]]) -> str:
    """messages: plus récent → plus ancien (API Discord) ; on inverse pour le log."""
    lines = ["---- LOG DE TICKET ----", ""]
    ordered = list(reversed(messages))
    for msg in ordered:
        author = _message_author_label(msg)
        stamp = _message_timestamp_local(msg)
        content = (msg.get("content") or "").strip()
        embeds = msg.get("embeds") or []
        bits: list[str] = []
        if content:
            bits.append(content)
        for emb in embeds:
            title = (emb.get("title") or "").strip()
            desc = (emb.get("description") or "").strip()
            if title or desc:
                bits.append(f"<EMBED {title or 'sans titre'}>")
                if desc:
                    bits.append(desc)
        attachments = msg.get("attachments") or []
        for att in attachments:
            name = att.get("filename") or "fichier"
            bits.append(f"[fichier: {name}]")
        body = "\n".join(bits).strip() or "(message vide)"
        lines.append(f"{stamp} - {author}: {body}")
        lines.append("")
    text = "\n".join(lines).strip() + "\n"
    return text


def find_open_ticket_channel(
    channels: list[dict[str, Any]],
    *,
    category_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    topic_want = ticket_topic_for_user(user_id)
    for ch in channels:
        if int(ch.get("type", -1)) != 0:
            continue
        if str(ch.get("parent_id") or "") != str(category_id):
            continue
        if parse_ticket_opener_id(ch.get("topic")) == str(user_id):
            return ch
        # Fallback: topic exact
        if str(ch.get("topic") or "") == topic_want:
            return ch
    return None


# Keep merge_view import used for gates compatibility / potential reuse
__all__ = [
    "RECRUIT_OPEN",
    "RECRUIT_CLOSE",
    "PANEL_TITLE",
    "TICKET_TOPIC_PREFIX",
    "sanitize_ticket_channel_name",
    "ticket_topic_for_user",
    "parse_ticket_opener_id",
    "build_ticket_overwrites",
    "build_recruitment_panel_payload",
    "build_ticket_candidature_payload",
    "format_ticket_transcript",
    "find_open_ticket_channel",
    "merge_view_channel_overwrite",
]
