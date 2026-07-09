from mandi.publish import _spread


def _row(market, district, price, date="2026-07-08", variety="Other"):
    return {"market": market, "district": district, "modal_price": price,
            "date": date, "variety": variety, "min_price": price, "max_price": price}


def test_spread_basic():
    s = _spread({
        ("A", "M1"): _row("M1", "A", 40000),
        ("B", "M2"): _row("M2", "B", 50000),
        ("C", "M3"): _row("M3", "C", 45000),
    })
    assert s["high"]["market"] == "M2" and s["low"]["market"] == "M1"
    assert s["spread_pct"] == 25.0
    assert s["n_markets"] == 3 and s["n_excluded"] == 0


def test_spread_excludes_stale_markets():
    s = _spread({
        ("A", "M1"): _row("M1", "A", 40000),
        ("B", "M2"): _row("M2", "B", 50000),
        ("C", "OLD"): _row("OLD", "C", 99999, date="2026-01-01"),  # stale
    })
    assert s["high"]["market"] == "M2"
    assert s["n_markets"] == 2


def test_spread_excludes_variety_outliers():
    """Premium/product outliers (e.g. Rashi vs Sippegotu) are not a spread."""
    s = _spread({
        ("A", "M1"): _row("M1", "A", 40000),
        ("B", "M2"): _row("M2", "B", 50000),
        ("C", "HI"): _row("HI", "C", 300000),  # different product entirely
        ("D", "LO"): _row("LO", "D", 4000),
    })
    assert s["high"]["market"] == "M2" and s["low"]["market"] == "M1"
    assert s["n_excluded"] == 2


def test_spread_needs_two_markets():
    assert _spread({("A", "M1"): _row("M1", "A", 40000)}) is None
    assert _spread({}) is None
