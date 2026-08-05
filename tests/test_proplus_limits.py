"""Tests copie permissions rôle Pro → Pro+."""

from vinted_bot.db.user_filters import plan_filter_limit


def test_proplus_filter_limit_is_30() -> None:
    assert plan_filter_limit("elite") == 30
    assert plan_filter_limit("proplus") == 30
    assert plan_filter_limit("pro+") == 30
    assert plan_filter_limit("premium") == 10
