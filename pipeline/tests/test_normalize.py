from mandi.normalize import normalize_records, parse_arrival_date

FETCHED_AT = "2026-07-08T18:00:00+00:00"


def test_parse_dates():
    assert parse_arrival_date("08/07/2026").isoformat() == "2026-07-08"
    assert parse_arrival_date("2026-07-08").isoformat() == "2026-07-08"


def test_normalize_partitions_good_bad_and_out_of_scope(cfg, ogd_records):
    rows, quarantined = normalize_records(cfg, ogd_records, "ogd", FETCHED_AT)

    # 4 clean in-scope rows: coconut Mukkom, arecanut Kannur, pepper Wayanad,
    # pepper Perumbavoor (Ernakulam is a configured benchmark district)
    assert len(rows) == 4
    by_market = {r["market"]: r for r in rows}
    assert by_market["Mukkom Market"]["commodity_slug"] == "coconut"
    assert by_market["Mukkom Market"]["modal_price"] == 5100
    assert by_market["Kuthuparambu Market"]["commodity_slug"] == "arecanut"
    assert by_market["Pulpally Market"]["date"] == "2026-07-07"
    assert by_market["Perumbavoor Market"]["district"] == "Ernakulam"

    # quarantine reasons: bad date, min>max order, sanity range, future date
    reasons = sorted(q["reason"] for q in quarantined)
    assert "bad_date" in reasons
    assert "min_modal_max_order" in reasons
    assert any(r.startswith("outside_sanity_range") for r in reasons)
    assert "future_date" in reasons
    assert len(quarantined) == 4

    # out of scope (Pineapple) skipped silently: 9 - 4 good - 4 bad = 1
    assert len(ogd_records) - len(rows) - len(quarantined) == 1


def test_market_whitelist_and_state_guard(cfg):
    base = {
        "commodity": "Arecanut(Betelnut/Supari)", "variety": "Rashi", "grade": "FAQ",
        "arrival_date": "08/07/2026", "min_price": 42000, "max_price": 44000,
        "modal_price": 43000,
    }
    records = [
        # whitelisted market in benchmark district -> kept
        {**base, "state": "Karnataka", "district": "Uttara Kannada", "market": "Sirsi APMC"},
        # non-whitelisted market in same district -> skipped
        {**base, "state": "Karnataka", "district": "Uttara Kannada", "market": "Mundgod APMC"},
        # same district name but wrong state -> skipped by the state guard
        {**base, "state": "Maharashtra", "district": "Uttara Kannada", "market": "Sirsi APMC"},
    ]
    rows, quarantined = normalize_records(cfg, records, "ogd", FETCHED_AT)
    assert len(rows) == 1 and not quarantined
    assert rows[0]["market"] == "Sirsi APMC"
    assert rows[0]["district"] == "Uttara Kannada"


def test_rows_have_all_columns(cfg, ogd_records):
    from mandi.normalize import COLUMNS

    rows, _ = normalize_records(cfg, ogd_records, "ogd", FETCHED_AT)
    for row in rows:
        assert list(row) == COLUMNS
