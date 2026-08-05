"""Panneau salon Guide fiscalité — liens PDF dans l'embed Resello."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x2ECC71
PDF_MIME = "application/pdf"
GUIDES_DIR = "config/guides"
PANEL_TITLE = "🧳 Guide fiscalité Resello"

GUIDE_FILES: tuple[tuple[str, str, str], ...] = (
    (
        "guide_intro_resell.pdf",
        "Guide d'intro Resell & Vinted",
        "Les bases du reselling avant de te lancer — "
        "et comment savoir si la fiscalité te concerne.",
    ),
    (
        "guide_fiscalite_vinted_resell.pdf",
        "Guide fiscalité Vinted & Resell",
        "Déclaration, seuils **DAC7**, micro-entreprise, "
        "cotisations et bonnes pratiques.",
    ),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_guides_dir(raw: str | None = None) -> Path:
    settings = get_settings()
    text = (
        raw
        or getattr(settings, "fiscalite_guides_path", "")
        or GUIDES_DIR
    ).strip()
    path = Path(text)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def build_fiscalite_panel_payload(
    *,
    guide_links: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """Embed avec liens PDF **dans** le cadre."""
    blocks: list[str] = [
        "Tout ce qu'il faut savoir pour revendre **en règle** "
        "avec le fisc français.",
        "",
        "━━━━━━━━━━━━━━",
        "",
    ]
    for idx, (label, blurb, url) in enumerate(guide_links, start=1):
        link = f"➡️ [Télécharger le PDF]({url})" if url else "➡️ PDF joint ci-dessous"
        blocks.extend(
            [
                f"📄 **{idx}. {label}**",
                blurb,
                link,
                "",
            ]
        )
    blocks.extend(
        [
            "━━━━━━━━━━━━━━",
            "",
            "_Document informatif — ne remplace pas un conseil professionnel._",
        ]
    )
    embed: dict[str, Any] = {
        "title": PANEL_TITLE,
        "description": "\n".join(blocks)[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "Resello · Guide fiscalité · Édition 2026"},
    }
    return {"embeds": [embed]}


def load_guide_files(
    *,
    base_dir: str | None = None,
) -> list[tuple[str, bytes, str, str]]:
    """filename, bytes, label, blurb."""
    root = resolve_guides_dir(base_dir)
    loaded: list[tuple[str, bytes, str, str]] = []
    for filename, label, blurb in GUIDE_FILES:
        path = root / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"Guide introuvable : {path}\nPlace {filename} dans {root}/"
            )
        data = path.read_bytes()
        if not data:
            raise ValueError(f"Guide vide : {path}")
        loaded.append((filename, data, label, blurb))
    return loaded


def _purge_fiscalite_channel(client: Any, channel_id: str) -> None:
    """Supprime les anciens panneaux bot / webhook du salon fiscalité."""
    from vinted_bot.config import discord_application_id
    from vinted_bot.interactions.discord_api import parse_discord_webhook_url

    token = getattr(client.settings, "discord_bot_token", "") or ""
    bot_id = discord_application_id(token)
    settings = get_settings()
    webhook = parse_discord_webhook_url(
        getattr(settings, "discord_webhook_fiscalite", "") or ""
    )
    try:
        response = client._client.get(
            f"/channels/{channel_id}/messages",
            params={"limit": 30},
        )
        if response.status_code != 200:
            return
        for msg in response.json():
            author = msg.get("author") or {}
            author_id = str(author.get("id") or "")
            embeds = msg.get("embeds") or []
            is_panel = any(
                str(e.get("title") or "") == PANEL_TITLE for e in embeds
            )
            is_bot = bool(bot_id and author_id == bot_id)
            is_webhook = bool(author.get("bot")) and is_panel
            if not (is_bot or is_webhook or is_panel):
                continue
            deleted = False
            if webhook and is_panel:
                wh_id, wh_token = webhook
                try:
                    del_r = client._client.delete(
                        f"/webhooks/{wh_id}/{wh_token}/messages/{msg['id']}"
                    )
                    deleted = del_r.status_code < 400
                except Exception:  # noqa: BLE001
                    deleted = False
            if not deleted:
                try:
                    client.delete_channel_message(channel_id, str(msg["id"]))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        log.debug("fiscalite_purge_failed", error=str(exc)[:120])


def post_fiscalite_panel(
    client: Any,
    *,
    channel_id: str,
    guild_id: str,
    webhook_url: str | None = None,
    guides_dir: str | None = None,
    purge_bot_messages: bool = True,
) -> dict[str, Any]:
    """Publie le panneau fiscalité dans ce salon uniquement (pas le salon host)."""
    from vinted_bot.interactions.discord_api import parse_discord_webhook_url

    if purge_bot_messages:
        _purge_fiscalite_channel(client, channel_id)

    guides = load_guide_files(base_dir=guides_dir)
    attachments = [(fn, data, PDF_MIME) for fn, data, _label, _blurb in guides]
    meta = [(fn, label, blurb) for fn, _data, label, blurb in guides]

    parsed = parse_discord_webhook_url(webhook_url or "")
    guild_name, _, logo_url = client.fetch_guild_branding(guild_id)

    # 1) Poster les PDF dans le salon fiscalité (pas CATALOG_HOST)
    draft_links = [(label, blurb, "") for _fn, label, blurb in meta]
    draft = build_fiscalite_panel_payload(guide_links=draft_links)
    embeds = list(draft.get("embeds") or [])
    if logo_url and embeds:
        embeds = client._apply_guild_logo_to_intro(
            embeds,
            guild_name=guild_name,
            icon_url=logo_url,
        )

    if parsed:
        wh_id, wh_token = parsed
        body: dict[str, Any] = {
            "username": guild_name,
            "embeds": embeds,
        }
        if logo_url:
            body["avatar_url"] = logo_url
        message = client.post_webhook_with_attachments(
            wh_id,
            wh_token,
            body,
            attachments=attachments,
        )
    else:
        message = client.post_channel_payload_with_attachments(
            channel_id,
            {"embeds": embeds},
            attachments=attachments,
        )

    # 2) Remplir les liens CDN dans l'embed (même message)
    by_name = {
        str(a.get("filename") or ""): str(a.get("url") or "")
        for a in (message.get("attachments") or [])
    }
    guide_links: list[tuple[str, str, str]] = []
    for filename, label, blurb in meta:
        guide_links.append((label, blurb, by_name.get(filename, "")))

    final = build_fiscalite_panel_payload(guide_links=guide_links)
    final_embeds = list(final.get("embeds") or [])
    if logo_url and final_embeds:
        final_embeds = client._apply_guild_logo_to_intro(
            final_embeds,
            guild_name=guild_name,
            icon_url=logo_url,
        )

    message_id = str(message.get("id") or "")
    if parsed and message_id:
        message = client.edit_webhook_message(
            wh_id,
            wh_token,
            message_id,
            {"embeds": final_embeds},
        )
    elif message_id:
        message = client.edit_channel_message(
            channel_id,
            message_id,
            {"embeds": final_embeds},
        )

    log.info(
        "fiscalite_panel_posted",
        channel_id=channel_id,
        message_id=message.get("id"),
        guides=len(guide_links),
        host="fiscalite_channel",
    )
    return message
