"""Gateway Discord pour les interactions de filtres et alertes."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import httpx
import websockets

from vinted_bot.config import get_settings
from vinted_bot.interactions.discord_api import DiscordInteractionClient
from vinted_bot.interactions.handlers import dispatch_interaction
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

INTENT_GUILDS = 1 << 0


async def _heartbeat(ws: websockets.ClientConnection, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        await ws.send(json.dumps({"op": 1, "d": None}))


async def run_discord_gateway() -> None:
    settings = get_settings()
    token = settings.discord_bot_token.strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN manquant dans .env")

    async with httpx.AsyncClient(timeout=30.0) as http:
        gateway = await http.get(
            "https://discord.com/api/v10/gateway/bot",
            headers={"Authorization": f"Bot {token}"},
        )
        gateway.raise_for_status()
        gateway_url = gateway.json()["url"]

    with DiscordInteractionClient(settings) as client:
        client.register_slash_commands()

        async with websockets.connect(
            f"{gateway_url}?v=10&encoding=json",
            ping_interval=None,
        ) as ws:
            hello = json.loads(await ws.recv())
            heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
            heartbeat_task = asyncio.create_task(_heartbeat(ws, heartbeat_interval))

            identify: dict[str, Any] = {
                "op": 2,
                "d": {
                    "token": f"Bot {token}",
                    "intents": INTENT_GUILDS,
                    "properties": {
                        "os": "linux",
                        "browser": "vinted-bot",
                        "device": "vinted-bot",
                    },
                },
            }
            await ws.send(json.dumps(identify))
            log.info("discord_gateway_identify_sent")

            try:
                async for raw in ws:
                    payload = json.loads(raw)
                    op = payload.get("op")
                    event = payload.get("t")

                    if op == 11:
                        continue
                    if op == 0 and event == "READY":
                        log.info(
                            "discord_gateway_ready",
                            session=payload.get("d", {}).get("session_id"),
                        )
                        continue
                    if op == 0 and event == "INTERACTION_CREATE":
                        interaction = payload.get("d") or {}
                        already_deferred = _defer_interaction_immediately(client, interaction)
                        asyncio.create_task(
                            _dispatch_interaction_safe(
                                client,
                                interaction,
                                already_deferred=already_deferred,
                            )
                        )
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task


def _defer_interaction_immediately(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> bool:
    """Les interactions restantes répondent sans opération longue préalable."""
    return False


def _interaction_failure_response(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    error: str,
) -> None:
    token = interaction.get("token")
    if not token:
        return
    message = f"❌ Erreur : {error[:500]}"
    # Après defer, seul edit_original fonctionne (respond → 10062 Unknown interaction).
    if not client.edit_original(token, content=message):
        log.warning("interaction_failure_edit_failed", interaction_id=interaction.get("id"))


async def _dispatch_interaction_safe(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    """Exécute les handlers synchrones hors de la boucle événementielle."""
    try:
        await asyncio.to_thread(
            dispatch_interaction,
            client,
            interaction,
            already_deferred=already_deferred,
        )
    except Exception as exc:
        log.exception(
            "interaction_dispatch_failed",
            error=str(exc),
            interaction_id=interaction.get("id"),
        )
        try:
            await asyncio.to_thread(
                _interaction_failure_response,
                client,
                interaction,
                str(exc),
            )
        except Exception:
            pass


def run_discord_interactions() -> None:
    """Point d'entrée sync (CLI)."""
    log.info("discord_interactions_start")
    try:
        asyncio.run(run_discord_gateway())
    except KeyboardInterrupt:
        log.info("discord_interactions_stopped")
