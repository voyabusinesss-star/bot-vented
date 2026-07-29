"""Message d'intro permanent — salon Vintify (vintify.me)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2
DEFAULT_VINTIFY_URL = "https://vintify.me/"
PREVIEW_FILENAME = "vintify-preview.png"
PREVIEW_MIME = "image/png"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_vintify_preview_path(raw: str | None = None) -> Path:
    settings = get_settings()
    text = (raw or getattr(settings, "vintify_preview_image_path", "") or "").strip()
    if not text:
        text = f"config/{PREVIEW_FILENAME}"
    path = Path(text)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def load_vintify_preview_bytes(
    preview_path: str | None = None,
) -> tuple[bytes, str]:
    path = resolve_vintify_preview_path(preview_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Aperçu Vintify introuvable : {path}\n"
            f"Copie {PREVIEW_FILENAME} dans config/ puis relance post-vintify-intro."
        )
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Aperçu Vintify vide : {path}")
    filename = path.name or PREVIEW_FILENAME
    log.info("vintify_preview_loaded", path=str(path), bytes=len(data))
    return data, filename


def build_vintify_cta_block(site_url: str) -> str:
    url = (site_url or DEFAULT_VINTIFY_URL).strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return (
        "👉 **Essaie dès maintenant :**\n"
        f"[Vintify.me]({url})"
    )


def build_vintify_intro_payload(
    *,
    site_url: str = DEFAULT_VINTIFY_URL,
    with_preview_image: bool = True,
    preview_filename: str = PREVIEW_FILENAME,
) -> dict[str, Any]:
    """Embed + aperçu image + lien cliquable + bouton (logo Resello via webhook)."""
    url = (site_url or DEFAULT_VINTIFY_URL).strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    intro: dict[str, Any] = {
        "title": "📸 Vintify — Des photos Vinted qui vendent, grâce à l'IA",
        "description": (
            "**C'est quoi ?**\n"
            "Vintify transforme tes photos d'articles en visuels professionnels "
            "pour booster tes ventes sur Vinted.\n\n"
            "**Ce que tu peux faire :**\n"
            "• **Essayage virtuel** — visualise l'article porté sans passer par une séance photo\n"
            "• **Mannequins IA** — des photos pro sans studio ni photographe\n"
            "• **Amélioration automatique** — lumière, fond, netteté corrigés en un clic\n"
            "• **Rédaction d'annonces** — génère des descriptions qui donnent envie d'acheter\n\n"
            "**Résultat :** des annonces plus pro, plus vues, plus vendues."
        )[:3900],
        "color": EMBED_COLOR,
        "footer": {"text": "Vintify · Resello"},
        "fields": [
            {
                "name": "\u200b",
                "value": build_vintify_cta_block(url)[:1024],
                "inline": False,
            }
        ],
    }
    if with_preview_image:
        name = (preview_filename or PREVIEW_FILENAME).strip() or PREVIEW_FILENAME
        intro["image"] = {"url": f"attachment://{name}"}
    return {
        "embeds": [intro],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "🚀 ESSAYER VINTIFY",
                        "url": url,
                    }
                ],
            }
        ],
    }


def post_vintify_intro_message(
    client: Any,
    *,
    channel_id: str,
    guild_id: str,
    webhook_url: str,
    site_url: str | None = None,
    preview_path: str | None = None,
) -> dict[str, Any]:
    """Publie l'intro Vintify avec branding serveur (webhook) + aperçu image."""
    from vinted_bot.interactions.discord_api import parse_discord_webhook_url

    parsed = parse_discord_webhook_url(webhook_url)
    if not parsed:
        raise ValueError("DISCORD_WEBHOOK_VINTIFY manquant ou invalide")

    settings = get_settings()
    url = (site_url or getattr(settings, "vintify_site_url", "") or DEFAULT_VINTIFY_URL).strip()
    preview_bytes, preview_name = load_vintify_preview_bytes(preview_path)
    wh_id, wh_token = parsed
    guild_name, _, logo_url = client.fetch_guild_branding(guild_id)

    final = build_vintify_intro_payload(
        site_url=url,
        with_preview_image=True,
        preview_filename=preview_name,
    )
    embeds = list(final.get("embeds") or [])
    if logo_url and embeds:
        embeds = client._apply_guild_logo_to_intro(
            embeds,
            guild_name=guild_name,
            icon_url=logo_url,
        )

    webhook_body: dict[str, Any] = {
        "username": guild_name,
        "embeds": embeds,
        "components": final.get("components") or [],
    }
    if logo_url:
        webhook_body["avatar_url"] = logo_url

    posted = client.post_webhook_with_attachments(
        wh_id,
        wh_token,
        webhook_body,
        attachments=[(preview_name, preview_bytes, PREVIEW_MIME)],
    )
    log.info(
        "vintify_intro_posted",
        channel_id=channel_id,
        message_id=posted.get("id"),
        site_url=url,
        preview=preview_name,
    )
    return posted
