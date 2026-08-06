"""Tests verrouillage salons règlement."""

from vinted_bot.services.reglement_gates import (
    VIEW_CHANNEL,
    build_gated_overwrites,
    merge_view_channel_overwrite,
    resolve_membre_denied_channel_ids,
    resolve_membre_preview_channel_ids,
)


def test_merge_view_channel_deny() -> None:
    result = merge_view_channel_overwrite(
        [],
        target_id="123",
        target_type=0,
        allow_view=False,
    )
    assert result[0]["id"] == "123"
    assert int(result[0]["deny"]) & VIEW_CHANNEL
    assert not int(result[0]["allow"]) & VIEW_CHANNEL


def test_merge_preserves_other_bits() -> None:
    existing = [
        {
            "id": "123",
            "type": 0,
            "allow": str(1 << 11),  # SEND_MESSAGES
            "deny": "0",
        }
    ]
    result = merge_view_channel_overwrite(
        existing,
        target_id="123",
        target_type=0,
        allow_view=True,
    )
    assert int(result[0]["allow"]) & (1 << 11)
    assert int(result[0]["allow"]) & VIEW_CHANNEL


def test_build_gated_overwrites_public() -> None:
    overwrites = build_gated_overwrites(
        [],
        everyone_id="guild",
        member_role_id="membre",
        is_public=True,
    )
    ids = {ow["id"] for ow in overwrites}
    assert ids == {"guild", "membre"}
    for ow in overwrites:
        assert int(ow["allow"]) & VIEW_CHANNEL


def test_build_gated_overwrites_member_only() -> None:
    overwrites = build_gated_overwrites(
        [],
        everyone_id="guild",
        member_role_id="membre",
        is_public=False,
    )
    ids = {ow["id"] for ow in overwrites}
    assert ids == {"guild", "membre"}
    everyone = next(ow for ow in overwrites if ow["id"] == "guild")
    membre = next(ow for ow in overwrites if ow["id"] == "membre")
    assert int(everyone["deny"]) & VIEW_CHANNEL
    assert int(membre["allow"]) & VIEW_CHANNEL


def test_build_gated_overwrites_deny_member() -> None:
    overwrites = build_gated_overwrites(
        [],
        everyone_id="guild",
        member_role_id="membre",
        is_public=False,
        deny_member=True,
        bot_role_ids=["bot"],
    )
    everyone = next(ow for ow in overwrites if ow["id"] == "guild")
    membre = next(ow for ow in overwrites if ow["id"] == "membre")
    bot = next(ow for ow in overwrites if ow["id"] == "bot")
    assert int(everyone["deny"]) & VIEW_CHANNEL
    assert int(membre["deny"]) & VIEW_CHANNEL
    assert not int(membre["allow"]) & VIEW_CHANNEL
    assert int(bot["allow"]) & VIEW_CHANNEL


def test_resolve_preview_and_denied() -> None:
    class S:
        discord_category_accueil = "1001"
        discord_category_rejoindre = "1002"
        discord_category_support = "1003"
        discord_category_avant_gout = "1004"
        discord_category_private_tools = "1005"
        discord_channel_annonces = ""
        discord_channel_concours = ""
        discord_channel_support = ""
        discord_channel_recruitment = ""
        discord_channel_subscriptions = ""
        discord_channel_bot_preview = ""
        discord_channel_niches_demo = ""
        discord_channel_mes_alertes = "2003"
        discord_channel_logs = "2004"
        discord_channel_catalog_host = ""
        discord_channel_bienvenue = "2000"
        discord_channel_reglement = "2001"
        discord_channel_presentation = ""

    channels = [
        {"id": "1001", "type": 4, "name": "ACCUEIL"},
        {"id": "2002", "type": 0, "parent_id": "1001", "name": "annonces"},
        {"id": "3001", "type": 4, "name": "INDEMODABLES"},
        {"id": "3002", "type": 0, "parent_id": "3001", "name": "nike"},
        {"id": "1005", "type": 4, "name": "OUTILS"},
        {"id": "2003", "type": 0, "parent_id": "1005", "name": "alertes"},
    ]
    preview = resolve_membre_preview_channel_ids(S(), channels)
    assert "1001" in preview
    assert "2002" in preview
    assert "3002" not in preview

    public = {"2000", "2001"}
    denied = resolve_membre_denied_channel_ids(
        S(),
        channels,
        public_channel_ids=public,
        preview_channel_ids=preview,
    )
    assert "3002" in denied
    assert "3001" in denied
    assert "2002" not in denied
    assert "2000" not in denied
