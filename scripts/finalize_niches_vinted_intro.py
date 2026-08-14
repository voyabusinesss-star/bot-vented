"""Finalise l'intro 1000 niches : garde le dernier message, supprime les doublons, épingle."""

from __future__ import annotations

import re
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.db.repositories import set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.interactions.discord_api import DiscordInteractionClient
from vinted_bot.interactions.niches_vinted_intro_panel import (
    CATALOG_HOST_CHECKPOINT,
    CATALOG_FILENAME,
    _purge_intro_channel_catalog_attachments,
)

INTRO_CHECKPOINT = "market:niches_vinted:intro_msg"
_INTRO_TITLE_MARKERS = ("1000 niches", "niches vinted")


def _message_intro_score(message: dict[str, Any]) -> tuple[int, int]:
    """Plus haut = meilleur candidat intro canonique."""
    score = 0
    embeds = message.get("embeds") or []
    if embeds:
        title = str(embeds[0].get("title") or "").lower()
        if any(marker in title for marker in _INTRO_TITLE_MARKERS):
            score += 10
        if embeds[0].get("fields"):
            score += 2
    components = message.get("components") or []
    for row in components:
        for comp in row.get("components") or []:
            if comp.get("type") == 2 and comp.get("url"):
                score += 5
    attachments = message.get("attachments") or []
    if any(str(a.get("filename") or "") == CATALOG_FILENAME for a in attachments):
        score += 1
    return score, int(message.get("id") or 0)


def _extract_download_url(message: dict[str, Any]) -> str:
    for row in message.get("components") or []:
        for comp in row.get("components") or []:
            url = str(comp.get("url") or "").strip()
            if url.startswith("http"):
                return url
    for embed in message.get("embeds") or []:
        url = str(embed.get("url") or "").strip()
        if url.startswith("http"):
            return url
        for field in embed.get("fields") or []:
            value = str(field.get("value") or "")
            match = re.search(r"\]\((https?://[^)]+)\)", value)
            if match:
                return match.group(1)
        description = str(embed.get("description") or "")
        match = re.search(r"\]\((https?://[^)]+)\)", description)
        if match:
            return match.group(1)
    for attachment in message.get("attachments") or []:
        url = str(attachment.get("url") or "").strip()
        if url.startswith("http"):
            return url
    return ""


def _is_intro_candidate(message: dict[str, Any]) -> bool:
    score, _ = _message_intro_score(message)
    return score >= 10


def main() -> None:
    settings = get_settings()
    with DiscordInteractionClient(settings) as client:
        channel_id = client.niches_vinted_channel_id()
        if not channel_id:
            raise SystemExit("DISCORD_CHANNEL_NICHES_VINTED manquant")

        response = client._client.get(
            f"/channels/{channel_id}/messages",
            params={"limit": 50},
        )
        if response.status_code >= 400:
            raise SystemExit(
                f"Lecture salon {response.status_code}: {response.text[:200]}"
            )

        messages = response.json()
        candidates = [m for m in messages if _is_intro_candidate(m)]
        if not candidates:
            raise SystemExit("Aucun message intro 1000 niches trouvé dans le salon")

        candidates.sort(key=_message_intro_score, reverse=True)
        keep = candidates[0]
        keep_id = str(keep.get("id") or "")
        download_url = _extract_download_url(keep)
        if not download_url:
            raise SystemExit(
                f"Message {keep_id} sans lien téléchargement détectable — "
                "ajoute le bouton ou le lien markdown puis relance."
            )

        deleted = 0
        for message in candidates[1:]:
            message_id = str(message.get("id") or "")
            if not message_id or message_id == keep_id:
                continue
            try:
                client.delete_channel_message(channel_id, message_id)
                deleted += 1
                print(f"deleted duplicate intro {message_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"skip delete {message_id}: {exc}")

        _purge_intro_channel_catalog_attachments(client, channel_id)

        pin_response = client._client.put(
            f"/channels/{channel_id}/pins/{keep_id}",
        )
        if pin_response.status_code >= 400:
            print(
                f"pin skipped ({pin_response.status_code}): "
                f"{pin_response.text[:200]} — épingle le message {keep_id} à la main "
                "ou accorde « Gérer les messages » au bot."
            )
        else:
            print(f"pinned intro message {keep_id}")

        with session_scope() as session:
            from vinted_bot.db.repositories import get_checkpoint

            set_checkpoint(
                session,
                INTRO_CHECKPOINT,
                {
                    "channel_id": channel_id,
                    "message_id": keep_id,
                    "url": download_url,
                },
            )
            catalog_meta = get_checkpoint(session, CATALOG_HOST_CHECKPOINT) or {}
            if isinstance(catalog_meta, dict):
                catalog_meta = dict(catalog_meta)
                catalog_meta["url"] = download_url
                set_checkpoint(session, CATALOG_HOST_CHECKPOINT, catalog_meta)

        print(f"download_url={download_url[:120]}")
        print(f"channel_id={channel_id}")
        print(f"deleted_duplicates={deleted}")


if __name__ == "__main__":
    main()
