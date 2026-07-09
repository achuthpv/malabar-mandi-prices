"""Kerala DES bulletin parser tests, pinned to real extracted PDF text."""

from pathlib import Path

import pytest

from mandi.des import DesError, _to_ogd_shape, parse_bulletin_text
from mandi.normalize import normalize_records

FIXTURE = (Path(__file__).parent / "fixtures" / "des_bulletin.txt").read_text()


def _rows(cfg, date, entries, fetched_at="2026-07-09T12:00:00+00:00"):
    shaped = _to_ogd_shape(cfg, date, entries)
    return normalize_records(cfg, shaped, source="des", fetched_at=fetched_at)


def test_parses_real_bulletin(cfg):
    date, entries = parse_bulletin_text(FIXTURE)
    assert date == "2026-07-09"
    assert len(entries) > 300  # the bulletin covers ~150 items

    rows, quarantined = _rows(cfg, date, entries)
    assert quarantined == []
    assert 15 <= len(rows) <= 60

    by_key = {(r["district"], r["commodity_slug"], r["variety"]): r["modal_price"]
              for r in rows}
    # the Kozhikode prices this PDF actually contains
    assert by_key[("Kozhikode", "arecanut", "Dry New")] == 36000
    assert by_key[("Kozhikode", "arecanut", "Dry Old")] == 36000
    assert by_key[("Kozhikode", "black-pepper", "Wayanadan")] == 68000
    # Thalassery maps into Kannur district
    assert by_key[("Kannur", "arecanut", "Dry Old")] == 38000
    for r in rows:
        assert r["source"] == "des" and r["grade"] == "DES"
        assert r["min_price"] == r["modal_price"] == r["max_price"]


def test_adjacent_items_do_not_leak(cfg):
    """'Rubber RSS 4' and 'Cotton Yarn-20 - One Box' follow pepper/arecanut
    tables in the bulletin; a heading mismatch once leaked their cheap rows
    into our commodities. Pin the fix."""
    date, entries = parse_bulletin_text(FIXTURE)
    rows, _ = _rows(cfg, date, entries)
    for r in rows:
        if r["commodity_slug"] == "black-pepper":
            assert r["modal_price"] > 40000, r
        if r["commodity_slug"] == "arecanut":
            assert r["modal_price"] > 20000, r
    items = {e["item"] for e in entries}
    assert "Rubber RSS 4" in items  # parsed as its own (unmapped) item
    assert any(i.startswith("Cotton Yarn-20") for i in items)


def test_non_bulletin_rejected():
    with pytest.raises(DesError):
        parse_bulletin_text("RUBBER TIMBER PRICE : 09-07-2026\nwhatever")


def test_markets_without_reports_are_skipped():
    text = ("DAILY MARKET WHOLESALE PRICE : 01-07-2026\n"
            "Arecanut Dry New - Quintal\n"
            "1\nSilentTown\n"          # no prices -> no entry
            "2\nKozhikode\n100.00\n200.00\n100.00\n")
    _, entries = parse_bulletin_text(text)
    assert len(entries) == 1
    assert entries[0]["market"] == "Kozhikode"
    assert entries[0]["price"] == 200.0  # middle value = current day


def test_page_break_noise_does_not_split_entry():
    text = ("DAILY MARKET WHOLESALE PRICE : 01-07-2026\n"
            "Arecanut Dry New - Quintal\n"
            "5\nKozhikode\n"
            "Sl\nNo\nItem\nPrice on - 30-06-2026\nPrice on - 01-07-2026\nVariation\n"
            "35000.00\n36000.00\n1000.00\n")
    _, entries = parse_bulletin_text(text)
    assert entries == [{"item": "Arecanut Dry New", "unit": "Quintal",
                        "market": "Kozhikode", "price": 36000.0}]


def test_two_value_rows_disambiguated():
    """2 decimals are ambiguous: (prev, current) when variation is blank,
    (current, variation) for a newly surveyed market with no previous day.
    The old code always took the second value — storing the VARIATION as
    the price. Pin the magnitude-based disambiguation."""
    def entry_price(a, b):
        text = ("DAILY MARKET WHOLESALE PRICE : 01-07-2026\n"
                "Arecanut Dry New - Quintal\n"
                f"5\nKozhikode\n{a}\n{b}\n")
        _, entries = parse_bulletin_text(text)
        return entries[0]["price"]

    assert entry_price("35000.00", "36000.00") == 36000.0  # (prev, current)
    assert entry_price("36000.00", "0.00") == 36000.0      # (current, variation=0)
    assert entry_price("36000.00", "500.00") == 36000.0    # (current, variation)
    assert entry_price("36000.00", "-500.00") == 36000.0   # (current, negative var)


def test_future_dated_bulletin_quarantined(cfg):
    """DES rows now flow through normalize_records, so the future_date
    guard applies to them like every other source."""
    text = ("DAILY MARKET WHOLESALE PRICE : 01-01-2030\n"
            "Arecanut Dry New - Quintal\n"
            "5\nKozhikode\n35000.00\n36000.00\n1000.00\n")
    date, entries = parse_bulletin_text(text)
    rows, quarantined = _rows(cfg, date, entries)
    assert rows == []
    assert quarantined and quarantined[0]["reason"] == "future_date"


def test_unmapped_item_or_removed_commodity_is_skipped_not_fatal(cfg):
    """An item heading with no des_items mapping (e.g. after a commodity is
    removed from sources.yaml) must be skipped, not crash the cron."""
    text = ("DAILY MARKET WHOLESALE PRICE : 01-07-2026\n"
            "Some Unmapped Item - Quintal\n"
            "5\nKozhikode\n35000.00\n36000.00\n1000.00\n")
    date, entries = parse_bulletin_text(text)
    assert len(entries) == 1
    rows, quarantined = _rows(cfg, date, entries)
    assert rows == [] and quarantined == []


def test_probe_mode_does_not_skip_unpublished_ids(cfg, monkeypatch, tmp_path):
    """When the listing page is down, state must only advance past ids that
    actually EXISTED — an id that 404s today may be published tomorrow."""
    from mandi import des

    monkeypatch.setattr(des, "STATE_PATH", tmp_path / "state.json")
    des._save_state({"last_id": 100})
    monkeypatch.setattr(des, "discover_bulletins", lambda s: [])  # listing down
    existing = {101: ("ok", ("2026-07-01", [])), 102: ("other", None)}
    monkeypatch.setattr(des, "_read_bulletin",
                        lambda s, i: existing.get(i, ("missing", None)))
    monkeypatch.setattr(des, "upsert_rows", lambda rows: {})
    monkeypatch.setattr(des, "write_quarantine", lambda q: None)
    monkeypatch.setattr(des.time, "sleep", lambda s: None)

    des.fetch(cfg)
    # 103..115 were probed but missing — state stops at the last EXISTING id
    assert des._load_state()["last_id"] == 102


def test_listing_mode_state_is_authoritative(cfg, monkeypatch, tmp_path):
    from mandi import des

    monkeypatch.setattr(des, "STATE_PATH", tmp_path / "state.json")
    des._save_state({"last_id": 100})
    monkeypatch.setattr(des, "discover_bulletins", lambda s: [(120, "2026-07-01")])
    monkeypatch.setattr(des, "_read_bulletin", lambda s, i: ("missing", None))
    monkeypatch.setattr(des, "upsert_rows", lambda rows: {})
    monkeypatch.setattr(des, "write_quarantine", lambda q: None)
    monkeypatch.setattr(des.time, "sleep", lambda s: None)

    des.fetch(cfg)
    # listing said 120 exists: ids <= 120 are allocated, safe to advance
    assert des._load_state()["last_id"] == 120
