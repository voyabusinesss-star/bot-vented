"""Tests partition sticky + parsing proxies."""

from __future__ import annotations

from vinted_bot.config_loader import SearchTarget
from vinted_bot.jobs.scrape_workers import partition_targets
from vinted_bot.utils.proxy import (
    assign_proxy_for_worker,
    parse_proxy_url_list,
    playwright_proxy_from_url,
    rotate_proxy,
)


def _target(brand: str, priority: str = "medium", catalog: list[int] | None = None) -> SearchTarget:
    return SearchTarget(
        brand=brand,
        query=brand,
        priority=priority,
        catalog_ids=catalog or [4, 5],
        enabled=True,
    )


def test_partition_targets_sticky_and_balanced() -> None:
    targets = [
        _target("nike", "high"),
        _target("adidas", "high"),
        _target("carhartt", "high"),
        _target("lacoste", "medium"),
        _target("ganni", "low"),
        _target("ugg", "low"),
    ]
    groups = partition_targets(targets, 3)
    assert len(groups) == 3
    flat = [t.brand for g in groups for t in g]
    assert sorted(flat) == sorted(t.brand for t in targets)
    # Round-robin équitable — tailles proches
    assert all(len(g) >= 1 for g in groups)
    sizes = [len(g) for g in groups]
    assert max(sizes) - min(sizes) <= 1


def test_partition_targets_single_worker() -> None:
    targets = [_target("nike"), _target("adidas")]
    groups = partition_targets(targets, 1)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_parse_proxy_url_list_csv_and_newlines() -> None:
    assert parse_proxy_url_list("") == []
    assert parse_proxy_url_list("http://a:1, http://b:2") == [
        "http://a:1",
        "http://b:2",
    ]
    assert parse_proxy_url_list("http://a:1\nhttp://b:2") == [
        "http://a:1",
        "http://b:2",
    ]


def test_playwright_proxy_from_url_with_auth() -> None:
    d = playwright_proxy_from_url("http://user:p%40ss@1.2.3.4:8080")
    assert d["server"] == "http://1.2.3.4:8080"
    assert d["username"] == "user"
    assert d["password"] == "p@ss"


def test_assign_and_rotate_proxy() -> None:
    proxies = ["http://a:1", "http://b:2", "http://c:3"]
    assert assign_proxy_for_worker(proxies, 0) == "http://a:1"
    assert assign_proxy_for_worker(proxies, 3) == "http://a:1"
    assert assign_proxy_for_worker([], 0) is None
    assert rotate_proxy(proxies, "http://a:1") == "http://b:2"
    assert rotate_proxy(proxies, "http://c:3") == "http://a:1"
    assert rotate_proxy([], None) is None
