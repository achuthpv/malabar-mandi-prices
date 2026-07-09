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


def test_spread_never_pairs_one_market_with_itself():
    """Rows are per market×variety; two varieties of one town must not be
    presented as a cross-market gap, and n_markets counts real markets."""
    s = _spread({
        ("A", "M1", "Dry New"): _row("M1", "A", 36000, variety="Dry New"),
        ("A", "M1", "Dry Old"): _row("M1", "A", 60000, variety="Dry Old"),
        ("B", "M2", "Dry New"): _row("M2", "B", 40000, variety="Dry New"),
    })
    assert s is not None
    assert s["high"]["market"] != s["low"]["market"]
    assert s["n_markets"] == 2  # distinct markets, not variety-rows
    # M1's representative price is the median of its varieties
    m1 = s["high"] if s["high"]["market"] == "M1" else s["low"]
    assert m1["modal_price"] in (36000, 60000)  # median of two = lower-middle


def test_spread_single_market_multiple_varieties_is_none():
    s = _spread({
        ("A", "M1", "V1"): _row("M1", "A", 36000, variety="V1"),
        ("A", "M1", "V2"): _row("M1", "A", 42000, variety="V2"),
    })
    assert s is None


def test_variety_summary():
    from mandi.publish import _variety_summary

    def crow(variety, price, date="2026-07-08"):
        return {"date": date, "district": "A", "market": "M1", "variety": variety,
                "modal_price": price}

    rows = [
        crow("Rashi", 48000), crow("Rashi", 50000),
        crow("Chali", 38000),
        crow("Chali", 99999, date="2020-01-01"),  # outside 30-day window
    ]
    out = _variety_summary(rows)
    assert [v["variety"] for v in out] == ["Rashi", "Chali"]  # highest median first
    rashi = out[0]
    assert rashi["median_modal"] == 49000 and rashi["n_obs"] == 2
    assert out[1]["n_obs"] == 1  # old row excluded
    assert _variety_summary([]) == []
