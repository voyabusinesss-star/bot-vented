"""Chargement des recherches configurables (YAML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vinted_bot.notify.discord import normalize_brand

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(slots=True)
class PriorityPolicy:
    every_n_cycles: int = 1
    extra_passes: int = 0
    max_items: int | None = None
    # Plafond Discord par marque et passage (évite les rafales après attente)
    max_discord_posts: int | None = None


@dataclass(slots=True)
class SearchTarget:
    brand: str
    query: str
    enabled: bool = True
    priority: str = "medium"
    brand_ids: list[int] = field(default_factory=list)
    catalog_ids: list[int] = field(default_factory=list)
    order: str = "newest_first"
    # override optionnel de la politique de priorité
    every_n_cycles: int | None = None
    extra_passes: int | None = None
    max_items: int | None = None
    max_discord_posts: int | None = None


@dataclass(slots=True)
class SearchesConfig:
    searches: list[SearchTarget]
    priorities: dict[str, PriorityPolicy] = field(default_factory=dict)
    max_items: int = 24
    delay_between_searches_seconds: float = 2.0
    loop_interval_seconds: float = 90.0
    browser_restart_every_cycles: int = 15
    reconnect_delay_seconds: float = 15.0
    order: str = "newest_first"
    slow_cycle_factor: float = 2.0
    max_discord_posts: int = 2


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _as_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    return []


def _default_priorities() -> dict[str, PriorityPolicy]:
    return {
        "high": PriorityPolicy(
            every_n_cycles=1, extra_passes=0, max_items=4, max_discord_posts=2
        ),
        "medium": PriorityPolicy(
            every_n_cycles=1, extra_passes=0, max_items=3, max_discord_posts=2
        ),
        "low": PriorityPolicy(
            every_n_cycles=1, extra_passes=0, max_items=2, max_discord_posts=1
        ),
    }


def _parse_priorities(raw: dict[str, Any] | None) -> dict[str, PriorityPolicy]:
    policies = _default_priorities()
    if not raw:
        return policies
    for name, conf in raw.items():
        if not isinstance(conf, dict):
            continue
        base = policies.get(str(name), PriorityPolicy())
        policies[str(name)] = PriorityPolicy(
            every_n_cycles=max(1, int(conf.get("every_n_cycles", base.every_n_cycles))),
            extra_passes=max(0, int(conf.get("extra_passes", base.extra_passes))),
            max_items=(
                int(conf["max_items"])
                if conf.get("max_items") is not None
                else base.max_items
            ),
            max_discord_posts=(
                int(conf["max_discord_posts"])
                if conf.get("max_discord_posts") is not None
                else base.max_discord_posts
            ),
        )
    return policies


def load_searches_config(path: Path | None = None) -> SearchesConfig:
    config_path = path or (_project_root() / "config" / "searches.yaml")
    if not config_path.exists():
        return SearchesConfig(searches=[], priorities=_default_priorities())

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    default_order = str(defaults.get("order", "newest_first"))
    # 4 = Femmes > Vêtements, 5 = Hommes > Vêtements (exclut chaussures / sacs)
    default_catalog_ids = _as_int_list(
        defaults.get("catalog_ids") or defaults.get("catalog")
    )
    default_max_discord = int(defaults.get("max_discord_posts", 2))
    priorities = _parse_priorities(raw.get("priorities"))

    searches: list[SearchTarget] = []
    for entry in raw.get("searches") or []:
        if not isinstance(entry, dict):
            continue
        brand = normalize_brand(str(entry.get("brand") or ""))
        query = str(entry.get("query") or brand).strip()
        if not brand or not query:
            continue
        priority = str(entry.get("priority") or "medium").lower().strip()
        if priority not in priorities:
            priority = "medium"
        entry_catalog_ids = _as_int_list(
            entry.get("catalog_ids") or entry.get("catalog")
        )
        searches.append(
            SearchTarget(
                brand=brand,
                query=query,
                enabled=bool(entry.get("enabled", True)),
                priority=priority,
                brand_ids=_as_int_list(entry.get("brand_ids")),
                catalog_ids=entry_catalog_ids or list(default_catalog_ids),
                order=str(entry.get("order") or default_order),
                every_n_cycles=(
                    int(entry["every_n_cycles"])
                    if entry.get("every_n_cycles") is not None
                    else None
                ),
                extra_passes=(
                    int(entry["extra_passes"])
                    if entry.get("extra_passes") is not None
                    else None
                ),
                max_items=(
                    int(entry["max_items"])
                    if entry.get("max_items") is not None
                    else None
                ),
                max_discord_posts=(
                    int(entry["max_discord_posts"])
                    if entry.get("max_discord_posts") is not None
                    else None
                ),
            )
        )

    return SearchesConfig(
        searches=searches,
        priorities=priorities,
        max_items=int(defaults.get("max_items", 24)),
        delay_between_searches_seconds=float(
            defaults.get("delay_between_searches_seconds", 2)
        ),
        loop_interval_seconds=float(defaults.get("loop_interval_seconds", 90)),
        browser_restart_every_cycles=int(
            defaults.get("browser_restart_every_cycles", 15)
        ),
        reconnect_delay_seconds=float(
            defaults.get("reconnect_delay_seconds", 15)
        ),
        order=default_order,
        slow_cycle_factor=float(defaults.get("slow_cycle_factor", 2.0)),
        max_discord_posts=default_max_discord,
    )


def active_searches_for_channels(
    channel_map: dict[str, str],
    *,
    path: Path | None = None,
) -> list[SearchTarget]:
    """Recherches enabled dont la marque a un salon Discord configuré."""
    cfg = load_searches_config(path)
    active: list[SearchTarget] = []
    for target in cfg.searches:
        if not target.enabled:
            continue
        if target.brand not in channel_map:
            continue
        active.append(target)
    return active


def resolve_policy(
    target: SearchTarget, priorities: dict[str, PriorityPolicy]
) -> PriorityPolicy:
    base = priorities.get(target.priority) or priorities.get("medium") or PriorityPolicy()
    return PriorityPolicy(
        every_n_cycles=target.every_n_cycles or base.every_n_cycles,
        extra_passes=(
            base.extra_passes
            if target.extra_passes is None
            else target.extra_passes
        ),
        max_items=target.max_items if target.max_items is not None else base.max_items,
        max_discord_posts=(
            target.max_discord_posts
            if target.max_discord_posts is not None
            else base.max_discord_posts
        ),
    )


def select_targets_for_cycle(
    cycle: int,
    targets: list[SearchTarget],
    priorities: dict[str, PriorityPolicy],
) -> list[SearchTarget]:
    """
    Sélectionne les recherches dues pour ce cycle, triées par priorité.
    - every_n_cycles=1 → chaque cycle
    - every_n_cycles=2 → cycles 1,3,5...
    - extra_passes → passages supplémentaires dans le même cycle
    """
    selected: list[SearchTarget] = []
    for target in targets:
        policy = resolve_policy(target, priorities)
        every = max(1, policy.every_n_cycles)
        if (cycle - 1) % every != 0:
            continue
        passes = 1 + max(0, policy.extra_passes)
        for _ in range(passes):
            selected.append(target)

    selected.sort(
        key=lambda t: (PRIORITY_RANK.get(t.priority, 9), t.brand, t.query)
    )
    return selected
