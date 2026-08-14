"""Force re-upload du catalogue 1000 niches + repost intro Discord."""

from __future__ import annotations

from vinted_bot.config import get_settings
from vinted_bot.db.repositories import set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.interactions.discord_api import DiscordInteractionClient, sanitize_guild_id
from vinted_bot.interactions.niches_vinted_intro_panel import (
    CATALOG_HOST_CHECKPOINT,
    _catalog_host_meta,
    _purge_intro_channel_catalog_attachments,
    _resolve_catalog_host_channel,
    build_niches_vinted_intro_payload,
    ensure_catalog_download_url,
    load_niches_vinted_catalog_bytes,
    post_niches_vinted_intro_message,
)


def main() -> None:
    settings = get_settings()
    catalog_bytes, catalog_name = load_niches_vinted_catalog_bytes()
    meta = _catalog_host_meta()

    with DiscordInteractionClient(settings) as client:
        if meta.get("channel_id") and meta.get("message_id"):
            try:
                client.delete_channel_message(meta["channel_id"], meta["message_id"])
                print(f"deleted host message {meta['message_id']}")
            except Exception as exc:  # noqa: BLE001
                print(f"delete host failed: {exc}")

        with session_scope() as session:
            set_checkpoint(session, CATALOG_HOST_CHECKPOINT, {})

        channel_id = client.niches_vinted_channel_id()
        if not channel_id:
            raise SystemExit("DISCORD_CHANNEL_NICHES_VINTED manquant")

        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        webhook_url = (getattr(settings, "discord_webhook_niches_vinted", "") or "").strip()

        if guild_id and webhook_url:
            message = post_niches_vinted_intro_message(
                client,
                channel_id=channel_id,
                guild_id=guild_id,
                webhook_url=webhook_url,
                catalog_bytes=catalog_bytes,
                catalog_filename=catalog_name,
            )
        else:
            host_channel_id = _resolve_catalog_host_channel(channel_id)
            _purge_intro_channel_catalog_attachments(client, channel_id)
            download_url = ensure_catalog_download_url(
                client,
                host_channel_id,
                catalog_bytes=catalog_bytes,
                catalog_filename=catalog_name,
            )
            payload = build_niches_vinted_intro_payload(
                catalog_filename=catalog_name,
                download_url=download_url,
            )
            message = client.post_channel_payload_as_guild_with_attachments(
                channel_id,
                payload,
                guild_id=guild_id,
                webhook_url=webhook_url,
                attachments=None,
            )

        new_meta = _catalog_host_meta()
        if new_meta.get("channel_id") and new_meta.get("message_id"):
            response = client._client.get(
                f"/channels/{new_meta['channel_id']}/messages/{new_meta['message_id']}"
            )
            if response.status_code == 200:
                attachments = response.json().get("attachments") or []
                if attachments:
                    print(f"host attachment size={attachments[0].get('size')}")
                    print(f"host url={attachments[0].get('url', '')[:120]}")

        print(f"intro message_id={message.get('id')}")
        print(f"channel_id={channel_id}")


if __name__ == "__main__":
    main()
