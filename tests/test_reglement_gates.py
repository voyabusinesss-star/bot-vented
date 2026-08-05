"""Tests verrouillage salons règlement."""

from vinted_bot.services.reglement_gates import (
    VIEW_CHANNEL,
    build_gated_overwrites,
    merge_view_channel_overwrite,
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
