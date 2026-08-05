"""Réponses API Discord pour les interactions de filtres et alertes."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from vinted_bot.config import Settings, discord_application_id, get_settings
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DISCORD_API = "https://discord.com/api/v10"
EPHEMERAL = 1 << 6
GUILD_LOGO_FILENAME = "guild_logo.png"
_WEBHOOK_URL_RE = re.compile(
    r"^https?://(?:discord(?:app)?\.com)/api/webhooks/(\d+)/([A-Za-z0-9_-]+)/?$"
)


class DiscordInteractionClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.token = self.settings.discord_bot_token.strip()
        self.application_id = discord_application_id(self.token)
        self._client = httpx.Client(
            base_url=DISCORD_API,
            headers={"Authorization": f"Bot {self.token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DiscordInteractionClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def register_slash_commands(self) -> None:
        commands = [
            {
                "name": "filtres",
                "description": "Liste TES filtres privés (visible uniquement par toi)",
                "type": 1,
            },
            {
                "name": "filtre-creer",
                "description": "Crée un filtre privé (alertes en DM uniquement)",
                "type": 1,
                "options": [
                    {
                        "name": "marque",
                        "description": "Ex. Nike, Arc'teryx",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "modele",
                        "description": "Ex. TN, Alpha SV",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "categorie",
                        "description": "Ex. veste, hoodie",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "mot_cle",
                        "description": "Ex. Jellycat",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "prix_max",
                        "description": "Prix maximum en euros",
                        "type": 10,
                        "required": False,
                    },
                    {
                        "name": "prix_min",
                        "description": "Prix minimum en euros",
                        "type": 10,
                        "required": False,
                    },
                    {
                        "name": "nom",
                        "description": "Nom du filtre (optionnel)",
                        "type": 3,
                        "required": False,
                    },
                ],
            },
            {
                "name": "filtre-supprimer",
                "description": "Supprime un de TES filtres privés",
                "type": 1,
                "options": [
                    {
                        "name": "id",
                        "description": "ID du filtre (voir /filtres)",
                        "type": 4,
                        "required": True,
                    }
                ],
            },
            {
                "name": "filtre-toggle",
                "description": "Active / désactive un de TES filtres",
                "type": 1,
                "options": [
                    {
                        "name": "id",
                        "description": "ID du filtre (voir /filtres)",
                        "type": 4,
                        "required": True,
                    }
                ],
            },
            {
                "name": "filtre-plan",
                "description": "Affiche ton abonnement et la limite de filtres",
                "type": 1,
            },
            {
                "name": "set-plan",
                "description": "Admin — définit le plan d'un membre (starter/premium/elite)",
                "type": 1,
                "options": [
                    {
                        "name": "user",
                        "description": "Membre Discord",
                        "type": 6,
                        "required": True,
                    },
                    {
                        "name": "plan",
                        "description": "starter | premium | elite",
                        "type": 3,
                        "required": True,
                        "choices": [
                            {"name": "Starter (0 filtre privé)", "value": "starter"},
                            {"name": "Pro (10 filtres)", "value": "premium"},
                            {"name": "Pro+ (30 filtres)", "value": "elite"},
                        ],
                    },
                ],
            },
        ]
        guild_id = sanitize_guild_id(self.settings.discord_guild_id)
        if guild_id:
            path = f"/applications/{self.application_id}/guilds/{guild_id}/commands"
        else:
            path = f"/applications/{self.application_id}/commands"
        response = self._client.put(path, json=commands)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Enregistrement slash commands échoué: {response.status_code} {response.text[:400]}"
            )
        log.info("discord_slash_commands_registered", guild_id=guild_id or "global")

    def respond(
        self,
        interaction_id: str,
        interaction_token: str,
        *,
        response_type: int,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"type": response_type}
        if data is not None:
            payload["data"] = data
        response = self._client.post(
            f"/interactions/{interaction_id}/{interaction_token}/callback",
            json=payload,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Interaction callback {response.status_code}: {response.text[:400]}"
            )

    def respond_ephemeral(self, interaction_id: str, interaction_token: str, content: str) -> None:
        self.respond(
            interaction_id,
            interaction_token,
            response_type=4,
            data={"content": content[:2000], "flags": EPHEMERAL},
        )

    def respond_public(self, interaction_id: str, interaction_token: str, content: str) -> None:
        """Réponse visible par le salon (pas éphémère)."""
        self.respond(
            interaction_id,
            interaction_token,
            response_type=4,
            data={"content": content[:2000]},
        )

    def defer_ephemeral(self, interaction_id: str, interaction_token: str) -> None:
        self.respond(
            interaction_id,
            interaction_token,
            response_type=5,
            data={"flags": EPHEMERAL},
        )

    def edit_original(
        self,
        interaction_token: str,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> bool:
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content[:2000]
        if embeds is not None:
            body["embeds"] = embeds
        if components is not None:
            body["components"] = components
        response = self._client.patch(
            f"/webhooks/{self.application_id}/{interaction_token}/messages/@original",
            json=body,
        )
        if response.status_code >= 400:
            log.warning(
                "edit_interaction_failed",
                status=response.status_code,
                body=response.text[:200],
            )
            return False
        return True

    def respond_ephemeral_payload(
        self,
        interaction_id: str,
        interaction_token: str,
        data: dict[str, Any],
    ) -> None:
        payload = dict(data)
        payload["flags"] = payload.get("flags", 0) | EPHEMERAL
        self.respond(
            interaction_id,
            interaction_token,
            response_type=4,
            data=payload,
        )

    def respond_update_message(
        self,
        interaction_id: str,
        interaction_token: str,
        data: dict[str, Any],
    ) -> None:
        """Met à jour le message source (type 7) — ex. liste MES ALERTES."""
        self.respond(
            interaction_id,
            interaction_token,
            response_type=7,
            data=data,
        )

    def respond_modal(
        self,
        interaction_id: str,
        interaction_token: str,
        modal: dict[str, Any],
    ) -> None:
        """Ouvre un modal Discord (type 9)."""
        self.respond(
            interaction_id,
            interaction_token,
            response_type=9,
            data=modal,
        )

    def post_channel_message(self, channel_id: str, content: str) -> None:
        if not channel_id:
            return
        response = self._client.post(
            f"/channels/{channel_id}/messages",
            json={"content": content[:2000]},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Post channel {response.status_code}: {response.text[:400]}"
            )

    def post_channel_payload(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not channel_id:
            raise ValueError("channel_id manquant")
        response = self._client.post(
            f"/channels/{channel_id}/messages",
            json=payload,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Post channel {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def delete_channel_message(self, channel_id: str, message_id: str) -> None:
        if not channel_id or not message_id:
            return
        response = self._client.delete(
            f"/channels/{channel_id}/messages/{message_id}",
        )
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(
                f"Delete message {response.status_code}: {response.text[:400]}"
            )

    def edit_channel_message(
        self,
        channel_id: str,
        message_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not channel_id or not message_id:
            raise ValueError("channel_id ou message_id manquant")
        response = self._client.patch(
            f"/channels/{channel_id}/messages/{message_id}",
            json=payload,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Edit message {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        cid = str(channel_id or "").strip()
        if not cid:
            raise ValueError("channel_id manquant")
        response = self._client.get(f"/channels/{cid}")
        if response.status_code >= 400:
            raise RuntimeError(
                f"Get channel {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def list_guild_channels(self, guild_id: str) -> list[dict[str, Any]]:
        gid = sanitize_guild_id(guild_id)
        if not gid:
            raise ValueError("guild_id manquant")
        response = self._client.get(f"/guilds/{gid}/channels")
        if response.status_code >= 400:
            raise RuntimeError(
                f"List channels {response.status_code}: {response.text[:400]}"
            )
        data = response.json()
        return data if isinstance(data, list) else []

    def create_guild_channel(
        self,
        guild_id: str,
        *,
        name: str,
        parent_id: str | None = None,
        topic: str | None = None,
        permission_overwrites: list[dict[str, Any]] | None = None,
        channel_type: int = 0,
    ) -> dict[str, Any]:
        gid = sanitize_guild_id(guild_id)
        if not gid:
            raise ValueError("guild_id manquant")
        body: dict[str, Any] = {
            "name": str(name).strip()[:100],
            "type": int(channel_type),
        }
        if parent_id:
            body["parent_id"] = str(parent_id)
        if topic:
            body["topic"] = str(topic)[:1024]
        if permission_overwrites is not None:
            body["permission_overwrites"] = permission_overwrites
        response = self._client.post(f"/guilds/{gid}/channels", json=body)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Create channel {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def delete_channel(self, channel_id: str) -> None:
        cid = str(channel_id or "").strip()
        if not cid:
            return
        response = self._client.delete(f"/channels/{cid}")
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(
                f"Delete channel {response.status_code}: {response.text[:400]}"
            )

    def list_channel_messages(
        self,
        channel_id: str,
        *,
        limit: int = 100,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        cid = str(channel_id or "").strip()
        if not cid:
            return []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if before:
            params["before"] = str(before)
        response = self._client.get(f"/channels/{cid}/messages", params=params)
        if response.status_code >= 400:
            raise RuntimeError(
                f"List messages {response.status_code}: {response.text[:400]}"
            )
        data = response.json()
        return data if isinstance(data, list) else []

    def open_dm_channel(self, discord_user_id: int | str) -> str:
        uid = str(discord_user_id or "").strip()
        if not uid:
            raise ValueError("discord_user_id manquant")
        response = self._client.post(
            "/users/@me/channels",
            json={"recipient_id": uid},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Open DM {response.status_code}: {response.text[:400]}"
            )
        channel_id = str(response.json().get("id") or "")
        if not channel_id:
            raise RuntimeError("Open DM: channel id manquant")
        return channel_id

    def send_dm_payload(
        self,
        discord_user_id: int | str,
        payload: dict[str, Any],
        *,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        channel_id = self.open_dm_channel(discord_user_id)
        files = list(attachments or [])
        if files:
            return self.post_channel_payload_with_attachments(
                channel_id, payload, attachments=files
            )
        return self.post_channel_payload(channel_id, payload)

    @staticmethod
    def _multipart_payload(
        payload: dict[str, Any],
        attachments: list[tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        files: dict[str, Any] = {
            "payload_json": (None, json.dumps(payload), "application/json"),
        }
        for index, (filename, data, content_type) in enumerate(attachments):
            files[f"files[{index}]"] = (filename, data, content_type)
        return files

    def post_channel_payload_with_attachments(
        self,
        channel_id: str,
        payload: dict[str, Any],
        *,
        attachments: list[tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        """Poste embed + pièces jointes (multipart)."""
        if not channel_id:
            raise ValueError("channel_id manquant")
        if not attachments:
            return self.post_channel_payload(channel_id, payload)
        response = self._client.post(
            f"/channels/{channel_id}/messages",
            files=self._multipart_payload(payload, attachments),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Post channel {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def post_channel_payload_as_guild_with_attachments(
        self,
        channel_id: str,
        payload: dict[str, Any],
        *,
        guild_id: str,
        webhook_url: str | None = None,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """Poste avec branding serveur (webhook si dispo) + pièces jointes."""
        files = list(attachments or [])
        parsed_webhook = parse_discord_webhook_url(webhook_url or "")
        guild_name, logo_bytes, logo_url = self.fetch_guild_branding(guild_id)

        if parsed_webhook:
            wh_id, wh_token = parsed_webhook
            embeds = list(payload.get("embeds") or [])
            if logo_url and embeds:
                embeds = self._apply_guild_logo_to_intro(
                    embeds,
                    guild_name=guild_name,
                    icon_url=logo_url,
                )
            webhook_body: dict[str, Any] = {
                "username": guild_name,
                "embeds": embeds or payload.get("embeds"),
            }
            if payload.get("components"):
                webhook_body["components"] = payload["components"]
            if logo_url:
                webhook_body["avatar_url"] = logo_url
            if files:
                response = self._client.post(
                    f"/webhooks/{wh_id}/{wh_token}",
                    params={"wait": "true"},
                    files=self._multipart_payload(webhook_body, files),
                )
            else:
                response = self._client.post(
                    f"/webhooks/{wh_id}/{wh_token}",
                    params={"wait": "true"},
                    json=webhook_body,
                )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Webhook post {response.status_code}: {response.text[:400]}"
                )
            return response.json()

        if files:
            return self.post_channel_payload_with_attachments(
                channel_id, payload, attachments=files
            )
        return self.post_channel_payload_as_guild(
            channel_id, payload, guild_id=guild_id, webhook_url=webhook_url
        )

    def fetch_guild_branding(self, guild_id: str) -> tuple[str, bytes | None, str | None]:
        """Nom du serveur + logo (bytes + URL CDN)."""
        gid = sanitize_guild_id(guild_id)
        if not gid:
            return "Resello", None, None
        response = self._client.get(f"/guilds/{gid}")
        if response.status_code >= 400:
            log.warning(
                "guild_branding_fetch_failed",
                status=response.status_code,
                body=response.text[:160],
            )
            return "Resello", None, None
        data = response.json()
        name = str(data.get("name") or "Resello").strip()[:80] or "Resello"
        icon_hash = data.get("icon")
        if not icon_hash:
            return name, None, None
        icon_url = (
            f"https://cdn.discordapp.com/icons/{gid}/{icon_hash}.png?size=256"
        )
        try:
            icon_resp = httpx.get(icon_url, timeout=20.0)
            icon_resp.raise_for_status()
            return name, icon_resp.content, icon_url
        except Exception as exc:  # noqa: BLE001
            log.warning("guild_logo_download_failed", error=str(exc)[:160])
            return name, None, icon_url

    @staticmethod
    def _apply_guild_logo_to_intro(
        embeds: list[dict[str, Any]],
        *,
        guild_name: str,
        icon_url: str | None = None,
        with_attachment: bool = False,
    ) -> list[dict[str, Any]]:
        if not embeds:
            return embeds
        intro = dict(embeds[0])
        if icon_url:
            intro["thumbnail"] = {"url": icon_url}
            intro["author"] = {"name": guild_name, "icon_url": icon_url}
        elif with_attachment:
            intro["thumbnail"] = {"url": f"attachment://{GUILD_LOGO_FILENAME}"}
            intro["author"] = {
                "name": guild_name,
                "icon_url": f"attachment://{GUILD_LOGO_FILENAME}",
            }
        embeds[0] = intro
        return embeds

    def _post_webhook_message(
        self,
        webhook_id: str,
        webhook_token: str,
        webhook_body: dict[str, Any],
        *,
        logo_bytes: bytes | None,
    ) -> dict[str, Any]:
        if logo_bytes:
            response = self._client.post(
                f"/webhooks/{webhook_id}/{webhook_token}",
                params={"wait": "true"},
                files={
                    "payload_json": (
                        None,
                        json.dumps(webhook_body),
                        "application/json",
                    ),
                    "files[0]": (GUILD_LOGO_FILENAME, logo_bytes, "image/png"),
                },
            )
        else:
            response = self._client.post(
                f"/webhooks/{webhook_id}/{webhook_token}",
                params={"wait": "true"},
                json=webhook_body,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Webhook post {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def post_webhook_with_attachments(
        self,
        webhook_id: str,
        webhook_token: str,
        webhook_body: dict[str, Any],
        *,
        attachments: list[tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/webhooks/{webhook_id}/{webhook_token}",
            params={"wait": "true"},
            files=self._multipart_payload(webhook_body, attachments),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Webhook post {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def edit_webhook_message(
        self,
        webhook_id: str,
        webhook_token: str,
        message_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._client.patch(
            f"/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
            json=body,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Webhook edit {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def post_channel_payload_with_guild_logo(
        self,
        channel_id: str,
        payload: dict[str, Any],
        *,
        guild_id: str,
    ) -> dict[str, Any]:
        """Logo serveur en pièce jointe (thumbnail + author dans l'embed intro)."""
        if not channel_id:
            raise ValueError("channel_id manquant")

        guild_name, logo_bytes, logo_url = self.fetch_guild_branding(guild_id)
        body = dict(payload)
        embeds = list(body.get("embeds") or [])

        if embeds and (logo_url or logo_bytes):
            body["embeds"] = self._apply_guild_logo_to_intro(
                embeds,
                guild_name=guild_name,
                icon_url=logo_url,
                with_attachment=not bool(logo_url) and bool(logo_bytes),
            )
            if logo_url:
                response = self._client.post(
                    f"/channels/{channel_id}/messages",
                    json=body,
                )
            else:
                response = self._client.post(
                    f"/channels/{channel_id}/messages",
                    files={
                        "payload_json": (None, json.dumps(body), "application/json"),
                        "files[0]": (GUILD_LOGO_FILENAME, logo_bytes, "image/png"),
                    },
                )
        else:
            response = self._client.post(
                f"/channels/{channel_id}/messages",
                json=body,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Post channel {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def post_channel_payload_as_guild(
        self,
        channel_id: str,
        payload: dict[str, Any],
        *,
        guild_id: str,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        """Poste avec avatar/nom du serveur (webhook) + logo en pièce jointe."""
        if not channel_id:
            raise ValueError("channel_id manquant")

        guild_name, logo_bytes, logo_url = self.fetch_guild_branding(guild_id)
        body = dict(payload)
        embeds = list(body.get("embeds") or [])

        parsed_webhook = parse_discord_webhook_url(webhook_url or "")
        if parsed_webhook:
            wh_id, wh_token = parsed_webhook
            if logo_bytes and embeds:
                body["embeds"] = self._apply_guild_logo_to_intro(
                    embeds,
                    guild_name=guild_name,
                    icon_url=logo_url,
                )
            webhook_body: dict[str, Any] = {
                "username": guild_name,
                "embeds": body.get("embeds"),
            }
            if body.get("components"):
                webhook_body["components"] = body["components"]
            if logo_url:
                webhook_body["avatar_url"] = logo_url
            # Ne pas envoyer le logo en multipart si on a déjà l'URL CDN :
            # le multipart httpx vide parfois embeds/components.
            return self._post_webhook_message(
                wh_id,
                wh_token,
                webhook_body,
                logo_bytes=None if logo_url else (logo_bytes if embeds else None),
            )

        if logo_bytes and embeds:
            body["embeds"] = self._apply_guild_logo_to_intro(
                embeds,
                guild_name=guild_name,
                icon_url=logo_url,
                with_attachment=not logo_url,
            )

        wh_resp = self._client.post(
            f"/channels/{channel_id}/webhooks",
            json={"name": guild_name[:80]},
        )
        if wh_resp.status_code >= 400:
            log.info(
                "guild_webhook_unavailable_fallback_bot",
                status=wh_resp.status_code,
                hint=(
                    "Accorde « Gérer les webhooks » au rôle Resello, "
                    "ou renseigne DISCORD_WEBHOOK_NICHES_DEMO / "
                    "DISCORD_WEBHOOK_NICHES_VINTED dans .env."
                ),
            )
            return self.post_channel_payload_with_guild_logo(
                channel_id, body, guild_id=guild_id
            )

        wh = wh_resp.json()
        wh_id, wh_token = wh["id"], wh["token"]

        webhook_body = {
            "username": guild_name,
            "embeds": body.get("embeds"),
        }
        if body.get("components"):
            webhook_body["components"] = body["components"]
        if logo_url:
            webhook_body["avatar_url"] = logo_url

        try:
            return self._post_webhook_message(
                wh_id,
                wh_token,
                webhook_body,
                logo_bytes=None if logo_url else (logo_bytes if embeds else None),
            )
        finally:
            try:
                self._client.delete(f"/webhooks/{wh_id}")
            except Exception:  # noqa: BLE001
                pass

    def mes_alertes_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_mes_alertes", "") or ""
        )

    def reglement_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_reglement", "") or ""
        )

    def bienvenue_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_bienvenue", "") or ""
        )

    def reglement_verified_role_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_role_reglement_verified", "") or ""
        )

    def add_guild_member_role(
        self,
        guild_id: str,
        user_id: int | str,
        role_id: str,
    ) -> None:
        gid = sanitize_guild_id(guild_id)
        rid = str(role_id or "").strip()
        uid = str(user_id or "").strip()
        if not gid or not rid or not uid:
            raise ValueError("guild_id, user_id ou role_id manquant")
        response = self._client.put(
            f"/guilds/{gid}/members/{uid}/roles/{rid}",
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Add role {response.status_code}: {response.text[:400]}"
            )

    def remove_guild_member_role(
        self,
        guild_id: str,
        user_id: int | str,
        role_id: str,
    ) -> None:
        gid = sanitize_guild_id(guild_id)
        rid = str(role_id or "").strip()
        uid = str(user_id or "").strip()
        if not gid or not rid or not uid:
            raise ValueError("guild_id, user_id ou role_id manquant")
        response = self._client.delete(
            f"/guilds/{gid}/members/{uid}/roles/{rid}",
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Remove role {response.status_code}: {response.text[:400]}"
            )

    def edit_channel_permission(
        self,
        channel_id: str,
        overwrite_id: str,
        *,
        allow: str | int = "0",
        deny: str | int = "0",
        overwrite_type: int = 0,
    ) -> None:
        """PUT /channels/{channel.id}/permissions/{overwrite.id} (type 0 = rôle)."""
        cid = str(channel_id or "").strip()
        oid = str(overwrite_id or "").strip()
        if not cid or not oid:
            raise ValueError("channel_id ou overwrite_id manquant")
        response = self._client.put(
            f"/channels/{cid}/permissions/{oid}",
            json={
                "type": int(overwrite_type),
                "allow": str(allow),
                "deny": str(deny),
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Edit permission {response.status_code}: {response.text[:400]}"
            )

    def delete_channel_permission(self, channel_id: str, overwrite_id: str) -> None:
        cid = str(channel_id or "").strip()
        oid = str(overwrite_id or "").strip()
        if not cid or not oid:
            return
        response = self._client.delete(f"/channels/{cid}/permissions/{oid}")
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(
                f"Delete permission {response.status_code}: {response.text[:400]}"
            )

    def resello_vip_role_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_role_resello_vip", "") or ""
        )

    def niches_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_niches", "") or ""
        )

    def niches_demo_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_niches_demo", "") or ""
        )

    def fiches_produit_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_fiches_produit", "") or ""
        )

    def niches_vinted_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_niches_vinted", "") or ""
        )

    def vintify_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_vintify", "") or ""
        )

    def subscriptions_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_subscriptions", "") or ""
        )

    def fiscalite_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_fiscalite", "") or ""
        )

    def recruitment_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_recruitment", "") or ""
        )

    def recruitment_category_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_category_recruitment_tickets", "") or ""
        )

    def recruitment_staff_role_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_role_recruitment_staff", "") or ""
        )

    def support_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_support", "") or ""
        )

    def support_category_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        raw = getattr(self.settings, "discord_category_support_tickets", "") or ""
        cid = sanitize_discord_channel_id(raw)
        return cid or self.recruitment_category_id()

    def support_staff_role_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        raw = getattr(self.settings, "discord_role_support_staff", "") or ""
        rid = sanitize_discord_channel_id(raw)
        return rid or self.recruitment_staff_role_id()

    def fournisseurs_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        return sanitize_discord_channel_id(
            getattr(self.settings, "discord_channel_fournisseurs", "") or ""
        )

    def vinted_links_channel_id(self) -> str:
        from vinted_bot.config import sanitize_discord_channel_id

        links = sanitize_discord_channel_id(self.settings.discord_channel_vinted_links)
        if links:
            return links
        return sanitize_discord_channel_id(self.settings.discord_channel_logs)


def parse_discord_webhook_url(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    match = _WEBHOOK_URL_RE.match(str(raw).strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def sanitize_guild_id(raw: str | None) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    return text if text.isdigit() else ""
