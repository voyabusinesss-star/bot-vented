"""Tests priorités / sélection de cycle."""

from vinted_bot.config_loader import (
    PriorityPolicy,
    SearchTarget,
    load_searches_config,
    select_targets_for_cycle,
)


def test_select_targets_priority_schedule() -> None:
    priorities = {
        "high": PriorityPolicy(every_n_cycles=1, extra_passes=0),
        "medium": PriorityPolicy(every_n_cycles=2, extra_passes=0),
        "low": PriorityPolicy(every_n_cycles=4, extra_passes=0),
    }
    targets = [
        SearchTarget(brand="nike", query="nike", priority="high"),
        SearchTarget(brand="lacoste", query="lacoste", priority="medium"),
        SearchTarget(brand="columbia", query="columbia", priority="low"),
    ]

    c1 = [t.brand for t in select_targets_for_cycle(1, targets, priorities)]
    assert c1 == ["nike", "lacoste", "columbia"]

    c2 = [t.brand for t in select_targets_for_cycle(2, targets, priorities)]
    assert c2 == ["nike"]

    c3 = [t.brand for t in select_targets_for_cycle(3, targets, priorities)]
    assert "nike" in c3 and "lacoste" in c3
    assert "columbia" not in c3

    c4 = [t.brand for t in select_targets_for_cycle(4, targets, priorities)]
    assert c4 == ["nike"]

    c5 = [t.brand for t in select_targets_for_cycle(5, targets, priorities)]
    assert set(c5) == {"nike", "lacoste", "columbia"}


def test_extra_passes_duplicates_high_in_cycle() -> None:
    priorities = {
        "high": PriorityPolicy(every_n_cycles=1, extra_passes=1),
    }
    targets = [SearchTarget(brand="nike", query="nike", priority="high")]
    selected = select_targets_for_cycle(1, targets, priorities)
    assert len(selected) == 2
    assert selected[0].brand == "nike"


def test_yaml_priorities_loaded() -> None:
    cfg = load_searches_config()
    assert "high" in cfg.priorities
    assert cfg.priorities["high"].every_n_cycles == 1
    assert cfg.priorities["high"].poll_interval_seconds == 0.5
    assert cfg.priorities["medium"].poll_interval_seconds == 2.0
    assert cfg.priorities["high"].max_discord_posts == 8
    assert cfg.priorities["medium"].max_discord_posts == 5
    nike = next(s for s in cfg.searches if s.brand == "nike")
    assert nike.priority == "high"
    columbia = next(s for s in cfg.searches if s.brand == "columbia")
    assert columbia.priority in {"low", "medium"}
