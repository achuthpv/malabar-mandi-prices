import pytest

from mandi.config import ConfigError, load_config


def test_loads_and_validates(cfg):
    assert {c.slug for c in cfg.commodities} >= {"arecanut", "black-pepper", "coconut"}
    assert any(d.name == "Kozhikode" for d in cfg.districts)
    # feed spelling variants resolve
    assert cfg.district_by_ogd_name["kozhikode(calicut)"].name == "Kozhikode"
    assert cfg.commodity_by_ogd_name["black pepper"].slug == "black-pepper"


def test_slug_lookup(cfg):
    assert cfg.commodity("coconut").display == "Coconut"
    with pytest.raises(KeyError):
        cfg.commodity("does-not-exist")


def test_bad_config_rejected(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        """
source: {ogd_resource: x, base_url: y, state_names: [Keralam]}
region_label: test
districts: [{name: A, ogd_names: [a]}]
commodities:
  - {slug: "Bad Slug!", display: X, ogd_names: [x], unit: u, sanity: {min: 1, max: 2}}
"""
    )
    with pytest.raises(ConfigError):
        load_config(bad)
