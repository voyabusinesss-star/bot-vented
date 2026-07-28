"""Chargement config/niches.yaml (détecteur de niches)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True, frozen=True)
class NicheProbe:
    query: str
    label: str = ""


@dataclass(slots=True, frozen=True)
class DiscoveryCatalog:
    """Catalogue Vinted à balayer (id None = tout le site)."""

    catalog_id: int | None
    label: str = ""


@dataclass(slots=True)
class NichesConfig:
    probes: list[NicheProbe] = field(default_factory=list)
    catalog_ids: list[int] = field(default_factory=list)
    # Balayage multi-catégories (pas seulement vêtements / chaussures)
    discovery_catalogs: list[DiscoveryCatalog] = field(default_factory=list)
    max_items_per_probe: int = 48
    max_items_per_catalog: int = 36
    catalog_sweep_per_cycle: int = 4
    loop_interval_seconds: float = 300.0
    delay_between_probes_seconds: float = 3.0
    min_samples: int = 6
    min_margin_pct: float = 25.0
    min_margin_eur: float = 12.0
    niche_cooldown_hours: float = 12.0
    max_discord_posts_per_cycle: int = 5


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_niches_config(path: Path | None = None) -> NichesConfig:
    cfg_path = path or (_project_root() / "config" / "niches.yaml")
    if not cfg_path.is_file():
        return NichesConfig()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    probes_raw = raw.get("probes") or []
    probes: list[NicheProbe] = []
    for entry in probes_raw:
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or "").strip()
        if not query:
            continue
        label = str(entry.get("label") or query).strip()
        probes.append(NicheProbe(query=query, label=label))
    # [] = pas de filtre catalogue sur les probes (toutes catégories)
    if "catalog_ids" not in defaults or defaults.get("catalog_ids") is None:
        catalog_ids: list[int] = []
    else:
        catalog = defaults.get("catalog_ids") or []
        catalog_ids = [
            int(x) for x in catalog if str(x).isdigit() or isinstance(x, int)
        ]

    discovery_catalogs: list[DiscoveryCatalog] = []
    for entry in raw.get("discovery_catalogs") or []:
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id", entry.get("catalog_id"))
        label = str(entry.get("label") or "").strip()
        if raw_id in (None, "", "all", "*", 0, "0"):
            discovery_catalogs.append(
                DiscoveryCatalog(catalog_id=None, label=label or "Tout Vinted")
            )
            continue
        try:
            discovery_catalogs.append(
                DiscoveryCatalog(catalog_id=int(raw_id), label=label or str(raw_id))
            )
        except (TypeError, ValueError):
            continue
    if not discovery_catalogs:
        # Défaut multi-domaines FR (mode + hors mode)
        discovery_catalogs = [
            DiscoveryCatalog(None, "Tout Vinted"),
            DiscoveryCatalog(4, "Vêtements femmes"),
            DiscoveryCatalog(5, "Vêtements hommes"),
            DiscoveryCatalog(1231, "Chaussures femmes"),
            DiscoveryCatalog(1242, "Chaussures hommes"),
            DiscoveryCatalog(1193, "Enfants"),
            DiscoveryCatalog(1918, "Maison"),
        ]

    return NichesConfig(
        probes=probes,
        catalog_ids=catalog_ids,
        discovery_catalogs=discovery_catalogs,
        max_items_per_probe=int(defaults.get("max_items_per_probe", 48)),
        max_items_per_catalog=int(defaults.get("max_items_per_catalog", 36)),
        catalog_sweep_per_cycle=int(defaults.get("catalog_sweep_per_cycle", 4)),
        loop_interval_seconds=float(defaults.get("loop_interval_seconds", 300)),
        delay_between_probes_seconds=float(
            defaults.get("delay_between_probes_seconds", 3.0)
        ),
        min_samples=int(defaults.get("min_samples", 6)),
        min_margin_pct=float(defaults.get("min_margin_pct", 25)),
        min_margin_eur=float(defaults.get("min_margin_eur", 12)),
        niche_cooldown_hours=float(defaults.get("niche_cooldown_hours", 12)),
        max_discord_posts_per_cycle=int(defaults.get("max_discord_posts_per_cycle", 5)),
    )


def _as_dict(cfg: NichesConfig) -> dict[str, Any]:
    return {
        "probes": len(cfg.probes),
        "min_samples": cfg.min_samples,
        "min_margin_pct": cfg.min_margin_pct,
    }
