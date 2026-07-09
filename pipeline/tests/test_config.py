import pytest

from mandi.config import ConfigError, load_config


def test_loads_and_validates(cfg):
    assert {c.slug for c in cfg.commodities} >= {"arecanut", "black-pepper", "coconut"}
    assert any(d.name == "Kozhikode" for d in cfg.districts)
    # feed spelling variants resolve
    assert cfg.district_by_ogd_name["kozhikode(calicut)"].name == "Kozhikode"
    assert cfg.commodity_by_ogd_name["black pepper"].slug == "black-pepper"


def test_multi_state_benchmarks(cfg):
    # union of state spellings across groups, home state first
    assert cfg.state_names[0] == "Keralam"
    assert "Karnataka" in cfg.state_names
    sirsi_belt = cfg.district_by_ogd_name["uttara kannada"]
    assert sirsi_belt.benchmark
    assert sirsi_belt.state_aliases == ("karnataka",)
    # market whitelist: substring, case-insensitive
    assert sirsi_belt.accepts_market("Sirsi APMC")
    assert not sirsi_belt.accepts_market("Mundgod APMC")
    # home districts accept everything and are not benchmarks
    kozhikode = cfg.district_by_ogd_name["kozhikode"]
    assert not kozhikode.benchmark
    assert kozhikode.accepts_market("Any Market At All")


def test_slug_lookup(cfg):
    assert cfg.commodity("coconut").display == "Coconut"
    with pytest.raises(KeyError):
        cfg.commodity("does-not-exist")


def test_bad_config_rejected(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        """
source: {ogd_resource: x, base_url: y}
region_label: test
states:
  - names: [Keralam]
    districts: [{name: A, ogd_names: [a]}]
commodities:
  - {slug: "Bad Slug!", display: X, ogd_names: [x], unit: u, sanity: {min: 1, max: 2}}
"""
    )
    with pytest.raises(ConfigError):
        load_config(bad)


def test_des_mappings_loaded(cfg):
    # commodity des_items -> (Commodity, variety) map
    c, variety = cfg.des_item_map["Arecanut Dry Old"]
    assert c.slug == "arecanut" and variety == "Dry Old"
    assert cfg.des_item_map["Pepper (Wayanadan)"][0].slug == "black-pepper"
    # district des_markets town map
    assert cfg.district_by_des_market["thalassery"].name == "Kannur"


def test_duplicate_des_mappings_rejected(tmp_path):
    base = """
source: {ogd_resource: x, base_url: y}
region_label: test
states:
  - names: [Keralam]
    districts:
      - {name: A, ogd_names: [a], des_markets: [Town]}
      - {name: B, ogd_names: [b], des_markets: [%s]}
commodities:
  - {slug: one, display: X, ogd_names: [x], unit: u, sanity: {min: 1, max: 2},
     des_items: {"Item X": "V"}}
  - {slug: two, display: Y, ogd_names: [y], unit: u, sanity: {min: 1, max: 2},
     des_items: {%s}}
"""
    dup_town = tmp_path / "town.yaml"
    dup_town.write_text(base % ("Town", '"Item Y": "V"'))
    with pytest.raises(ConfigError, match="des_markets"):
        load_config(dup_town)

    dup_item = tmp_path / "item.yaml"
    dup_item.write_text(base % ("OtherTown", '"Item X": "V"'))
    with pytest.raises(ConfigError, match="des_items"):
        load_config(dup_item)


def test_all_benchmark_config_rejected(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        """
source: {ogd_resource: x, base_url: y}
region_label: test
states:
  - names: [Keralam]
    districts: [{name: A, ogd_names: [a], benchmark: true}]
commodities:
  - {slug: ok, display: X, ogd_names: [x], unit: u, sanity: {min: 1, max: 2}}
"""
    )
    with pytest.raises(ConfigError):
        load_config(bad)
