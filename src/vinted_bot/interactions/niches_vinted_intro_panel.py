"""Message d'intro permanent — salon niches vinted."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vinted_bot.config import get_settings, sanitize_discord_channel_id
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2
CATALOG_FILENAME = "Resello_1000_Niches_Vinted.xlsx"
CATALOG_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
CATALOG_HOST_CHECKPOINT = "market:niches_vinted:catalog_host_msg"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_niches_vinted_catalog_path(raw: str | None = None) -> Path:
    settings = get_settings()
    text = (raw or getattr(settings, "niches_vinted_catalog_path", "") or "").strip()
    if not text:
        text = f"config/{CATALOG_FILENAME}"
    path = Path(text)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def load_niches_vinted_catalog_bytes(
    catalog_path: str | None = None,
) -> tuple[bytes, str]:
    path = resolve_niches_vinted_catalog_path(catalog_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Catalogue introuvable : {path}\n"
            f"Copie {CATALOG_FILENAME} dans config/ puis relance "
            "post-niches-vinted-intro."
        )
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Catalogue vide : {path}")
    filename = path.name or CATALOG_FILENAME
    log.info("niches_vinted_catalog_loaded", path=str(path), bytes=len(data))
    return data, filename


def build_niches_vinted_download_block(download_url: str) -> str:
    """Bloc téléchargement — phrase + lien cliquable (sans gras dans le markdown)."""
    if not download_url:
        return "👇 **Un clic, tout le catalogue.**"
    return (
        "👇 **Un clic, tout le catalogue.**\n"
        f"[📥 TÉLÉCHARGER LES 1000 NICHES]({download_url})"
    )


def build_niches_vinted_intro_payload(
    *,
    catalog_filename: str,
    download_url: str,
) -> dict[str, Any]:
    """Présentation (embed) + lien cliquable sous « Un clic… » + bouton."""
    _ = catalog_filename  # conservé pour l’API / tests
    intro: dict[str, Any] = {
        "title": "📊 1000 Niches Vinted Rentables — Accès Gratuit",
        "description": (
            "**C'est quoi ?**\n"
            "La base de données Resello : **1000 niches triées** pour repérer "
            "rapidement les articles qui se revendent bien sur Vinted.\n\n"
            "**À l'intérieur :**\n"
            "• **1000 niches classées** — vintage, streetwear, sneakers, "
            "outdoor, luxe accessible, Y2K, gaming, et bien plus\n"
            "• Pour chaque niche : **prix d'achat conseillé**, "
            "**prix de revente moyen** et **niveau de demande**\n"
            "• Format Excel, prêt à filtrer et trier — compatible PC et mobile\n"
            "• **100% gratuit**, sans limite d'utilisation."
        )[:3900],
        "color": EMBED_COLOR,
        "footer": {"text": "Niches Vinted · Resello"},
    }
    if download_url:
        intro["fields"] = [
            {
                "name": "\u200b",
                "value": build_niches_vinted_download_block(download_url)[:1024],
                "inline": False,
            }
        ]
    else:
        intro["fields"] = [
            {
                "name": "\u200b",
                "value": build_niches_vinted_download_block(""),
                "inline": False,
            }
        ]
    result: dict[str, Any] = {"embeds": [intro]}
    if download_url:
        result["components"] = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "📥 TÉLÉCHARGER LES 1000 NICHES",
                        "url": download_url,
                    }
                ],
            }
        ]
    return result


def _catalog_host_meta() -> dict[str, str]:
    from vinted_bot.db.repositories import get_checkpoint
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        data = get_checkpoint(session, CATALOG_HOST_CHECKPOINT) or {}
    if not isinstance(data, dict):
        return {}
    channel_id = str(data.get("channel_id") or "").strip()
    message_id = str(data.get("message_id") or "").strip()
    if channel_id.isdigit() and message_id.isdigit():
        return {"channel_id": channel_id, "message_id": message_id}
    return {}


def _save_catalog_host_meta(*, channel_id: str, message_id: str, url: str) -> None:
    from vinted_bot.db.repositories import set_checkpoint
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        set_checkpoint(
            session,
            CATALOG_HOST_CHECKPOINT,
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "url": url,
            },
        )


def _resolve_catalog_host_channel(intro_channel_id: str) -> str:
    """Salon technique pour héberger le xlsx (jamais le détecteur ni l'intro public)."""
    settings = get_settings()
    intro = sanitize_discord_channel_id(intro_channel_id)
    niches_detector = sanitize_discord_channel_id(
        getattr(settings, "discord_channel_niches", "") or ""
    )
    for raw in (
        getattr(settings, "discord_channel_catalog_host", ""),
        getattr(settings, "discord_channel_logs", ""),
        getattr(settings, "discord_channel_mes_alertes", ""),
    ):
        host = sanitize_discord_channel_id(str(raw or ""))
        if not host or host == intro or host == niches_detector:
            continue
        return host
    raise ValueError(
        "Configure DISCORD_CHANNEL_CATALOG_HOST (salon admin privé) "
        "pour héberger le catalogue — pas le salon 🧠 Détecteur."
    )


def _purge_channel_catalog_attachments(
    client: Any,
    channel_id: str,
) -> None:
    """Retire les fichiers xlsx catalogue visibles dans un salon."""
    channel = sanitize_discord_channel_id(channel_id)
    if not channel:
        return
    meta = _catalog_host_meta()
    if meta.get("channel_id") == channel and meta.get("message_id"):
        try:
            client.delete_channel_message(channel, meta["message_id"])
        except Exception:  # noqa: BLE001
            pass
    try:
        response = client._client.get(
            f"/channels/{channel}/messages",
            params={"limit": 25},
        )
        if response.status_code != 200:
            return
        for msg in response.json():
            if msg.get("embeds"):
                continue
            attachments = msg.get("attachments") or []
            if len(attachments) != 1:
                continue
            name = str(attachments[0].get("filename") or "")
            if name != CATALOG_FILENAME:
                continue
            try:
                client.delete_channel_message(channel, str(msg["id"]))
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.debug("catalog_channel_purge_failed", error=str(exc)[:120])


def _purge_intro_channel_catalog_attachments(
    client: Any,
    intro_channel_id: str,
) -> None:
    _purge_channel_catalog_attachments(client, intro_channel_id)


def ensure_catalog_download_url(
    client: Any,
    host_channel_id: str,
    *,
    catalog_bytes: bytes,
    catalog_filename: str,
) -> str:
    """Héberge le xlsx hors salon public et retourne l'URL CDN."""
    host_channel = sanitize_discord_channel_id(host_channel_id)
    meta = _catalog_host_meta()
    old_host = meta.get("channel_id") or ""
    old_msg = meta.get("message_id") or ""
    if old_host and old_msg and old_host != host_channel:
        try:
            client.delete_channel_message(old_host, old_msg)
        except Exception:  # noqa: BLE001
            pass
    if meta.get("channel_id") == host_channel and meta.get("message_id"):
        try:
            response = client._client.get(
                f"/channels/{host_channel}/messages/{meta['message_id']}"
            )
            if response.status_code == 200:
                attachments = response.json().get("attachments") or []
                if attachments and attachments[0].get("url"):
                    return str(attachments[0]["url"])
        except Exception as exc:  # noqa: BLE001
            log.debug("catalog_host_fetch_failed", error=str(exc)[:120])
        try:
            client.delete_channel_message(host_channel, meta["message_id"])
        except Exception:  # noqa: BLE001
            pass

    host_msg = client.post_channel_payload_with_attachments(
        host_channel,
        {"content": ""},
        attachments=[(catalog_filename, catalog_bytes, CATALOG_MIME)],
    )
    attachments = host_msg.get("attachments") or []
    if not attachments or not attachments[0].get("url"):
        raise RuntimeError("Upload catalogue : URL de pièce jointe manquante")
    url = str(attachments[0]["url"])
    message_id = str(host_msg.get("id") or "")
    if message_id:
        _save_catalog_host_meta(
            channel_id=host_channel,
            message_id=message_id,
            url=url,
        )
    log.info(
        "niches_vinted_catalog_hosted",
        channel_id=host_channel,
        message_id=message_id,
    )
    return url


def _clear_catalog_host_message(client: Any, channel_id: str) -> None:
    meta = _catalog_host_meta()
    if meta.get("channel_id") != channel_id or not meta.get("message_id"):
        return
    try:
        client.delete_channel_message(channel_id, meta["message_id"])
    except Exception:  # noqa: BLE001
        pass


def post_niches_vinted_intro_message(
    client: Any,
    *,
    channel_id: str,
    guild_id: str,
    webhook_url: str,
    catalog_bytes: bytes,
    catalog_filename: str,
) -> dict[str, Any]:
    """Poste l'intro + lien cliquable (fichier hébergé, message webhook unique)."""
    from vinted_bot.interactions.discord_api import parse_discord_webhook_url

    parsed = parse_discord_webhook_url(webhook_url)
    if not parsed:
        raise ValueError("DISCORD_WEBHOOK_NICHES_VINTED manquant ou invalide")

    wh_id, wh_token = parsed
    guild_name, _, logo_url = client.fetch_guild_branding(guild_id)
    host_channel_id = _resolve_catalog_host_channel(channel_id)
    settings = get_settings()
    detector_channel = sanitize_discord_channel_id(
        getattr(settings, "discord_channel_niches", "") or ""
    )
    _purge_intro_channel_catalog_attachments(client, channel_id)
    if detector_channel:
        _purge_channel_catalog_attachments(client, detector_channel)

    download_url = ensure_catalog_download_url(
        client,
        host_channel_id,
        catalog_bytes=catalog_bytes,
        catalog_filename=catalog_filename,
    )

    final = build_niches_vinted_intro_payload(
        catalog_filename=catalog_filename,
        download_url=download_url,
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

    response = client._client.post(
        f"/webhooks/{wh_id}/{wh_token}",
        params={"wait": "true"},
        json=webhook_body,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Webhook post {response.status_code}: {response.text[:400]}"
        )
    posted = response.json()
    log.info(
        "niches_vinted_intro_posted",
        channel_id=channel_id,
        message_id=posted.get("id"),
        download_url=download_url[:80],
    )
    return posted
