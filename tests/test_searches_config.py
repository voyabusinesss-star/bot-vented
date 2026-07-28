"""Tests config recherches YAML."""

from pathlib import Path

from vinted_bot.config_loader import active_searches_for_channels, load_searches_config


def test_load_searches_yaml() -> None:
    cfg = load_searches_config()
    assert cfg.max_items >= 1
    assert len(cfg.searches) >= 1
    brands = {s.brand for s in cfg.searches}
    assert "nike" in brands
    assert "carhartt" in brands
    # Toutes les recherches enabled ont des brand_ids Vinted
    enabled = [s for s in cfg.searches if s.enabled]
    assert enabled
    missing = [s.brand for s in enabled if not s.brand_ids]
    assert missing == [], f"brand_ids manquants: {missing}"
    nike = next(s for s in cfg.searches if s.brand == "nike")
    assert nike.brand_ids == [53]
    ralph = next(s for s in cfg.searches if s.brand == "ralph lauren")
    assert 88 in ralph.brand_ids and 4273 in ralph.brand_ids


def test_active_searches_filters_by_channel_map(tmp_path: Path) -> None:
    yaml_path = tmp_path / "searches.yaml"
    yaml_path.write_text(
        """
searches:
  - brand: nike
    query: nike vêtements
    enabled: true
  - brand: adidas
    query: adidas
    enabled: false
  - brand: puma
    query: puma
    enabled: true
defaults:
  max_items: 10
""",
        encoding="utf-8",
    )
    active = active_searches_for_channels(
        {"nike": "111", "adidas": "222"},
        path=yaml_path,
    )
    assert len(active) == 1
    assert active[0].brand == "nike"
    assert active[0].query == "nike vêtements"
