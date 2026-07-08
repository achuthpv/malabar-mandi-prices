"""Load and validate config/sources.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Repo root = three levels up from this file (pipeline/src/mandi/config.py)
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "sources.yaml"
DATA_DIR = REPO_ROOT / "data"
PRICES_DIR = DATA_DIR / "prices"
QUARANTINE_DIR = DATA_DIR / "quarantine"
SITE_DIR = REPO_ROOT / "site"
API_DIR = SITE_DIR / "api" / "v1"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Commodity:
    slug: str
    display: str
    ogd_names: tuple[str, ...]
    unit: str
    sanity_min: int
    sanity_max: int


@dataclass(frozen=True)
class District:
    name: str
    ogd_names: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    ogd_resource: str
    base_url: str
    state_names: tuple[str, ...]
    region_label: str
    districts: tuple[District, ...]
    commodities: tuple[Commodity, ...]
    # Derived lookup tables (lowercased ogd name -> canonical object)
    commodity_by_ogd_name: dict[str, Commodity] = field(default_factory=dict)
    district_by_ogd_name: dict[str, District] = field(default_factory=dict)

    def commodity(self, slug: str) -> Commodity:
        for c in self.commodities:
            if c.slug == slug:
                return c
        raise KeyError(f"unknown commodity slug: {slug}")


class ConfigError(ValueError):
    """Raised when sources.yaml is malformed."""


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    try:
        src = raw["source"]
        districts = tuple(
            District(name=d["name"], ogd_names=tuple(d["ogd_names"]))
            for d in raw["districts"]
        )
        commodities = tuple(
            Commodity(
                slug=c["slug"],
                display=c["display"],
                ogd_names=tuple(c["ogd_names"]),
                unit=c["unit"],
                sanity_min=int(c["sanity"]["min"]),
                sanity_max=int(c["sanity"]["max"]),
            )
            for c in raw["commodities"]
        )
        cfg = Config(
            ogd_resource=src["ogd_resource"],
            base_url=src["base_url"].rstrip("/"),
            state_names=tuple(src["state_names"]),
            region_label=raw["region_label"],
            districts=districts,
            commodities=commodities,
        )
    except (KeyError, TypeError) as e:
        raise ConfigError(f"sources.yaml is missing or has a malformed field: {e}") from e

    _validate(cfg)

    for c in cfg.commodities:
        for name in c.ogd_names:
            cfg.commodity_by_ogd_name[name.lower()] = c
    for d in cfg.districts:
        for name in d.ogd_names:
            cfg.district_by_ogd_name[name.lower()] = d
    return cfg


def _validate(cfg: Config) -> None:
    slugs = [c.slug for c in cfg.commodities]
    if len(slugs) != len(set(slugs)):
        raise ConfigError("duplicate commodity slugs")
    for c in cfg.commodities:
        if not _SLUG_RE.match(c.slug):
            raise ConfigError(f"invalid slug (must be kebab-case): {c.slug!r}")
        if not c.ogd_names:
            raise ConfigError(f"commodity {c.slug}: ogd_names must not be empty")
        if c.sanity_min <= 0 or c.sanity_max <= c.sanity_min:
            raise ConfigError(f"commodity {c.slug}: bad sanity range")
    if not cfg.districts:
        raise ConfigError("districts must not be empty")
    seen: set[str] = set()
    for c in cfg.commodities:
        for name in c.ogd_names:
            key = name.lower()
            if key in seen:
                raise ConfigError(f"ogd_name {name!r} mapped to more than one commodity")
            seen.add(key)
