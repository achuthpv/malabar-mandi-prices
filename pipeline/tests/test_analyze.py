"""Analysis tests on synthetic data with a known seasonal pattern."""

import math
from datetime import date, timedelta

import pytest

from mandi.analyze import analyze_all
from mandi.store import upsert_rows

TODAY = date(2026, 7, 8)


def _seasonal_price(day: date) -> int:
    """Base 10000 with a sinusoidal peak in December (month 12), trough in June."""
    phase = (day.month - 12) / 12 * 2 * math.pi
    seasonal = 1 + 0.10 * math.cos(phase)
    trend = 1 + 0.05 * (day.year - 2021)  # inflation-ish drift the index must ignore
    return int(10000 * seasonal * trend)


@pytest.fixture()
def seeded_base(tmp_path):
    rows = []
    day = date(2021, 1, 1)
    while day <= TODAY:
        if day.weekday() < 5:  # market days only; weekends are gaps
            price = _seasonal_price(day)
            rows.append({
                "date": day.isoformat(), "district": "Kozhikode",
                "market": "Mukkom Market", "commodity_slug": "coconut",
                "variety": "Other", "grade": "FAQ",
                "min_price": price - 200, "max_price": price + 200,
                "modal_price": price, "unit": "Rs/quintal",
                "source": "ogd", "fetched_at": "2026-07-08T00:00:00+00:00",
            })
        day += timedelta(days=1)
    upsert_rows(rows, base=tmp_path)
    return tmp_path


def test_seasonal_index_finds_december_peak(cfg, seeded_base):
    res = analyze_all(cfg, base=seeded_base, today=TODAY)
    region = res["commodities"]["coconut"]["region"]
    seasonality = region["seasonality"]
    assert seasonality is not None

    index = seasonality["index"]
    assert max(range(12), key=lambda m: index[m]) in (10, 11, 0)  # Nov-Jan peak
    assert min(range(12), key=lambda m: index[m]) in (4, 5, 6)  # May-Jul trough

    sell = seasonality["best_sell"]
    buy = seasonality["best_buy"]
    assert 12 in sell["months"] or 1 in sell["months"]
    assert 6 in buy["months"] or 7 in buy["months"]
    assert sell["premium_pct"] > 3
    assert buy["premium_pct"] < -3
    assert sell["confidence"] == "high"  # 5 clean years, tight IQR


def test_freshness_and_trend(cfg, seeded_base):
    res = analyze_all(cfg, base=seeded_base, today=TODAY)
    region = res["commodities"]["coconut"]["region"]
    assert region["freshness"]["days_stale"] <= 3
    assert not region["freshness"]["stale"]
    assert region["trend"]["ma30"] is not None
    assert region["latest"]["modal_price"] > 0
    assert region["narrative"], "narrative must not be empty"


def test_empty_commodity_has_no_region_analysis(cfg, seeded_base):
    res = analyze_all(cfg, base=seeded_base, today=TODAY)
    assert res["commodities"]["arecanut"]["region"] is None
    assert res["commodities"]["arecanut"]["districts"] == {}


def test_benchmark_district_excluded_from_region(cfg, tmp_path):
    """Sirsi-belt (benchmark) prices must not shift the home-region series."""
    def row(district, market, price):
        return {
            "date": "2026-07-01", "district": district, "market": market,
            "commodity_slug": "arecanut", "variety": "Other", "grade": "FAQ",
            "min_price": price - 100, "max_price": price + 100,
            "modal_price": price, "unit": "Rs/quintal",
            "source": "ogd", "fetched_at": "2026-07-08T00:00:00+00:00",
        }

    upsert_rows([
        row("Kasargod", "Kasargod Market", 40000),
        row("Kannur", "Kuthuparambu Market", 42000),
        row("Uttara Kannada", "Sirsi APMC", 90000),  # benchmark outlier
    ], base=tmp_path)

    res = analyze_all(cfg, base=tmp_path, today=TODAY)
    areca = res["commodities"]["arecanut"]
    # region latest = median of the two home districts only
    assert areca["region"]["latest"]["modal_price"] == 41000
    # but the benchmark district still gets its own analysis, flagged
    assert areca["districts"]["Uttara Kannada"]["benchmark"] is True
    assert areca["districts"]["Uttara Kannada"]["latest"]["modal_price"] == 90000
    assert areca["districts"]["Kasargod"]["benchmark"] is False


def test_cheap_variety_tier_excluded_from_pooled_series(cfg, tmp_path):
    """Fresh Ripe arecanut (~Rs7k) must not drag the pooled Dry (~Rs36k)
    series — variety composition shifts would otherwise fake seasonality."""
    def row(day, variety, price):
        return {
            "date": day, "district": "Kozhikode", "market": "Kozhikode",
            "commodity_slug": "arecanut", "variety": variety, "grade": "DES",
            "min_price": price, "max_price": price, "modal_price": price,
            "unit": "Rs/quintal", "source": "des",
            "fetched_at": "2026-07-08T00:00:00+00:00",
        }

    rows = []
    for m in range(1, 7):
        rows.append(row(f"2026-{m:02d}-10", "Dry New", 36000))
        rows.append(row(f"2026-{m:02d}-10", "Dry Old", 38000))
        # cheap product form reports intermittently — including a day when
        # ONLY Ripe reports, which used to punch a 7k hole in the series
        rows.append(row(f"2026-{m:02d}-1{m}", "Ripe", 7000))
    upsert_rows(rows, base=tmp_path)

    res = analyze_all(cfg, base=tmp_path, today=date(2026, 7, 8))
    region = res["commodities"]["arecanut"]["region"]
    # latest observation comes from the comparable (Dry) tier, not Ripe
    assert region["latest"]["modal_price"] >= 36000
    # ripe-only days are gaps, not 7k values: series has one point per month
    assert region["n_obs"] == 6


def test_sparse_history_degrades_gracefully(cfg, tmp_path):
    """A few months of data: no seasonality, but latest/freshness still work."""
    rows = [{
        "date": f"2026-0{m}-15", "district": "Kannur", "market": "Kannur Market",
        "commodity_slug": "black-pepper", "variety": "Other", "grade": "Local",
        "min_price": 60000, "max_price": 62000, "modal_price": 61000,
        "unit": "Rs/quintal", "source": "ogd",
        "fetched_at": "2026-07-08T00:00:00+00:00",
    } for m in range(1, 8)]
    upsert_rows(rows, base=tmp_path)

    res = analyze_all(cfg, base=tmp_path, today=TODAY)
    region = res["commodities"]["black-pepper"]["region"]
    assert region["seasonality"] is None
    assert region["latest"]["modal_price"] == 61000
    assert "Not enough multi-year history" in region["narrative"][0]
