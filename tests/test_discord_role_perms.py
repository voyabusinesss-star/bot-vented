"""Tests permissions rôles Discord (copie Pro → Starter / Pro+)."""

from vinted_bot.services.discord_role_perms import channel_ids_in_category


def test_channel_ids_in_category_includes_children() -> None:
    channels = [
        {"id": "cat1", "parent_id": None},
        {"id": "ch1", "parent_id": "cat1"},
        {"id": "ch2", "parent_id": "cat1"},
        {"id": "other", "parent_id": "cat2"},
    ]
    assert channel_ids_in_category(channels, "cat1") == {"cat1", "ch1", "ch2"}


def test_channel_ids_in_category_empty_when_no_id() -> None:
    assert channel_ids_in_category([{"id": "1"}], "") == set()
