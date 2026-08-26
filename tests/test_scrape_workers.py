"""Tests partition sticky + parsing proxies."""

from __future__ import annotations

from types import SimpleNamespace
from threading import Thread

from vinted_bot.config_loader import SearchTarget
from vinted_bot.jobs.scrape_workers import (
    TargetActivity,
    _activity_hotness,
    _adaptive_poll_interval,
    _filter_inject_batch_size,
    _pick_due_target,
    _target_key,
    partition_targets,
)
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


def test_parse_proxy_url_list_json_array_railway() -> None:
    raw = '["http://user:pass@p.webshare.io:80"]'
    assert parse_proxy_url_list(raw) == ["http://user:pass@p.webshare.io:80"]


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


def test_filter_inject_batch_size_caps_load() -> None:
    assert _filter_inject_batch_size(0) == 0
    assert _filter_inject_batch_size(1) == 1
    assert _filter_inject_batch_size(8) == 2
    assert _filter_inject_batch_size(20) == 3


def test_adaptive_poll_interval_speeds_up_hot_page() -> None:
    from vinted_bot.services.scrape_search import ScrapeActivitySignal

    target = _target("nike", "high")
    hot = ScrapeActivitySignal(
        newest_age_seconds=8.0,
        oldest_age_seconds=25.0,
        page_saturated=True,
    )
    assert _adaptive_poll_interval(target, hot) <= 4.0


def test_activity_hotness_prefers_fresh_listings() -> None:
    cold = TargetActivity(newest_age_s=800.0, updated_at=100.0)
    hot = TargetActivity(
        newest_age_s=15.0,
        oldest_age_s=40.0,
        page_saturated=True,
        items_created=2,
        updated_at=100.0,
    )
    now = 100.0
    assert _activity_hotness(hot, now=now) > _activity_hotness(cold, now=now)


def test_activity_hotness_unprobed_is_low_vs_hot_market() -> None:
    hot = TargetActivity(
        newest_age_s=8.0,
        page_saturated=True,
        oldest_age_s=20.0,
        updated_at=500.0,
    )
    assert _activity_hotness(None, now=500.0) < _activity_hotness(hot, now=500.0)


def test_activity_hotness_decays_between_scrapes() -> None:
    act = TargetActivity(newest_age_s=10.0, page_saturated=True, updated_at=100.0)
    assert _activity_hotness(act, now=100.0) > _activity_hotness(act, now=400.0)


def test_pick_due_same_list_order_irrelevant_with_activity() -> None:
    targets = [_target("acne studios", "high"), _target("nike", "high")]
    now = 500.0
    next_run = {_target_key(t): now - 1.0 for t in targets}
    activity = {
        _target_key(targets[1]): TargetActivity(
            newest_age_s=10.0,
            page_saturated=True,
            oldest_age_s=30.0,
            updated_at=now,
        ),
        _target_key(targets[0]): TargetActivity(
            newest_age_s=600.0,
            updated_at=now,
        ),
    }
    a = _pick_due_target(targets, next_run, now=now, activity=activity)
    b = _pick_due_target(list(reversed(targets)), next_run, now=now, activity=activity)
    assert a is not None and b is not None
    assert a.brand == b.brand == "nike"


def test_brand_worker_backoff_flag() -> None:
    from vinted_bot.jobs.scrape_workers import BrandWorker

    worker = BrandWorker(
        worker_id=0,
        targets=[],
        proxy_url=None,
        all_proxies=[],
        headless=True,
    )
    worker._backoff_until = __import__("time").monotonic() + 60.0
    assert worker.is_in_backoff() is True
    worker._backoff_until = 0.0
    assert worker.is_in_backoff() is False


def test_brand_worker_recreates_dead_browser() -> None:
    from vinted_bot.jobs.scrape_workers import BrandWorker

    worker = BrandWorker(
        worker_id=0,
        targets=[],
        proxy_url=None,
        all_proxies=[],
        headless=True,
    )
    dead = SimpleNamespace(_thread=SimpleNamespace(is_alive=lambda: False))
    worker._browser = dead  # type: ignore[assignment]
    assert worker._browser_is_usable() is False
    alive = SimpleNamespace(_thread=Thread(target=lambda: None, daemon=True))
    alive._thread.start()
    alive._thread.join(timeout=1.0)
    assert worker._browser_is_usable(alive) is False
