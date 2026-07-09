"""Kerala DES bulletin parser tests, pinned to real extracted PDF text."""

from pathlib import Path

import pytest

from mandi.des import DesError, _to_rows, parse_bulletin_text

FIXTURE = (Path(__file__).parent / "fixtures" / "des_bulletin.txt").read_text()


def test_parses_real_bulletin(cfg):
    date, entries = parse_bulletin_text(FIXTURE)
    assert date == "2026-07-09"
    assert len(entries) > 300  # the bulletin covers ~150 items

    rows, quarantined = _to_rows(cfg, date, entries, "2026-07-09T12:00:00+00:00")
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
    rows, _ = _to_rows(cfg, date, entries, "x")
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
