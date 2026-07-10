"""Alert rule evaluation and edge-trigger semantics."""

from mandi.alerts import _rule_key, evaluate


def _analysis(ma30=66000, vs_typical=None):
    signal = {"month": "Jul", "vs_typical_pct": vs_typical} if vs_typical is not None else None
    return {"commodities": {"black-pepper": {
        "region": {"trend": {"ma30": ma30}, "signal": signal},
        "districts": {"Wayanad": {"trend": {"ma30": ma30 + 2000}, "signal": signal}},
    }}}


def _rows(prices):
    return [{"date": "2026-07-08", "district": d, "market": m,
             "commodity_slug": "black-pepper", "variety": "Ungarbled",
             "grade": "DES", "min_price": p, "max_price": p, "modal_price": p,
             "unit": "Rs/quintal", "source": "des", "fetched_at": "x"}
            for (d, m, p) in prices]


def test_price_threshold_rules(cfg):
    rules = [
        {"type": "price_above", "commodity": "black-pepper", "threshold": 60000},
        {"type": "price_below", "commodity": "black-pepper", "threshold": 60000},
        {"type": "price_above", "commodity": "black-pepper",
         "district": "Wayanad", "threshold": 67000},
    ]
    results = evaluate(cfg, _analysis(ma30=66000), [], rules)
    fired = {key: f for key, f, _ in results}
    assert fired[_rule_key(rules[0])] is True   # 66000 > 60000
    assert fired[_rule_key(rules[1])] is False
    assert fired[_rule_key(rules[2])] is True   # district ma30 = 68000 > 67000
    msg = next(m for _, f, m in results if f and "ABOVE" in m)
    assert "Black Pepper" in msg and "66,000" in msg


def test_spread_rule_uses_distinct_markets(cfg):
    rules = [{"type": "spread_above", "commodity": "black-pepper", "pct": 20}]
    # 30% gap across two markets -> fires
    rows = _rows([("A", "M1", 60000), ("B", "M2", 78000)])
    (_, fired, msg), = evaluate(cfg, _analysis(), rows, rules)
    assert fired and "M2" in msg and "M1" in msg
    # same market, two varieties -> no cross-market spread -> silent
    rows_same = _rows([("A", "M1", 60000), ("A", "M1", 78000)])
    rows_same[1]["variety"] = "Garbled"
    (_, fired2, _), = evaluate(cfg, _analysis(), rows_same, rules)
    assert not fired2


def test_vs_typical_rules(cfg):
    rules = [
        {"type": "vs_typical_above", "commodity": "black-pepper", "pct": 10},
        {"type": "vs_typical_below", "commodity": "black-pepper", "pct": 10},
    ]
    up = evaluate(cfg, _analysis(vs_typical=14.0), [], rules)
    assert [f for _, f, _ in up] == [True, False]
    down = evaluate(cfg, _analysis(vs_typical=-14.0), [], rules)
    assert [f for _, f, _ in down] == [False, True]
    calm = evaluate(cfg, _analysis(vs_typical=3.0), [], rules)
    assert [f for _, f, _ in calm] == [False, False]


def test_unknown_rule_and_commodity_skipped(cfg):
    rules = [
        {"type": "does_not_exist", "commodity": "black-pepper"},
        {"type": "price_above", "commodity": "not-a-slug", "threshold": 1},
    ]
    assert evaluate(cfg, _analysis(), [], rules) == []


def test_edge_trigger_state(cfg, monkeypatch, tmp_path):
    """A persisting condition fires once; it re-arms after clearing."""
    from mandi import alerts

    monkeypatch.setattr(alerts, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(alerts, "OUTPUT_PATH", tmp_path / "alerts.txt")
    rules = [{"type": "price_above", "commodity": "black-pepper", "threshold": 60000}]
    monkeypatch.setattr(alerts, "_load_rules", lambda: rules)
    monkeypatch.setattr(alerts, "read_all_rows", lambda: [])

    def run_with(ma30):
        monkeypatch.setattr(alerts, "analyze_all",
                            lambda cfg_, today=None: _analysis(ma30=ma30))
        alerts.run(cfg)
        return (tmp_path / "alerts.txt").read_text()

    assert "ABOVE" in run_with(66000)   # fires on the edge
    assert run_with(66000) == ""        # persists -> silent
    assert run_with(50000) == ""        # clears -> silent, re-arms
    assert "ABOVE" in run_with(66000)   # fires again
