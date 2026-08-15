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
    deny_role_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """@everyone + rôles membres : deny view ; opener / bot / staff : allow."""
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
    role_ids: list[str] = []
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
    staff_set = set(role_ids)
    for rid in deny_role_ids or []:
        if not rid or rid in staff_set:
            continue
        overwrites.append(
            _perm_overwrite(
                target_id=rid,
                target_type=0,
                deny=VIEW_CHANNEL,
            )
        )
    return overwrites


def format_ticket_staff_mentions(role_ids: list[str]) -> str:
    return " ".join(f"<@&{rid}>" for rid in role_ids if rid)


def build_ticket_close_dm_payload(
    *,
    kind: str,
    channel_name: str = "",
) -> dict[str, Any]:
    """Message MP envoyé à l'auteur du ticket à la fermeture."""
    label = "aide" if kind == "aide" else "recrutement"
    title = "🎫 Ticket aide fermé" if kind == "aide" else "🎫 Ticket recrutement fermé"
    salon = f"**#{channel_name}**\n\n" if channel_name else ""
    return {
        "embeds": [
            {
                "title": title,
                "description": (
                    f"{salon}"
                    "Ton ticket Resello est **clos**.\n\n"
                    "📎 **Pièce jointe** : historique complet de la conversation "
                    "(texte brut, lisible sur mobile et PC).\n\n"
                    "_Merci d'avoir contacté le staff Resello._"
                )[:4096],
                "color": EMBED_COLOR,
                "footer": {"text": "Resello · Support"},
            }
        ],
    }


def ticket_transcript_filename(*, kind: str, channel_name: str = "") -> str:
    label = "aide" if kind == "aide" else "recrutement"
    safe = re.sub(r"[^a-z0-9_-]+", "-", (channel_name or label).lower()).strip("-")
    if not safe:
        safe = label
    stamp = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y%m%d")
    return f"resello-ticket-{safe}-{stamp}.txt"


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


def format_ticket_transcript(
    messages: list[dict[str, Any]],
    *,
    kind: str = "aide",
    channel_name: str = "",
) -> str:
    """messages: plus récent → plus ancien (API Discord) ; on inverse pour le log."""
    label = "aide" if kind == "aide" else "recrutement"
    header_name = channel_name or f"ticket-{label}"
    now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M")
    lines = [
        "Resello — Historique du ticket",
        f"Type : {label}",
        f"Salon : #{header_name}",
        f"Export : {now} (Europe/Paris)",
        "━" * 40,
        "",
    ]
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
            if title:
                bits.append(f"[{title}]")
            if desc:
                bits.append(desc)
        attachments = msg.get("attachments") or []
        for att in attachments:
            name = att.get("filename") or "fichier"
            bits.append(f"(pièce jointe : {name})")
        body = "\n".join(bits).strip() or "—"
        lines.append(f"[{stamp}] {author}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def find_open_ticket_channel(
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
    "build_ticket_close_dm_payload",
    "format_ticket_staff_mentions",
    "ticket_transcript_filename",
    "build_recruitment_panel_payload",
    "build_ticket_candidature_payload",
    "format_ticket_transcript",
    "find_open_ticket_channel",
    "merge_view_channel_overwrite",
]
