from mandi.normalize import normalize_records
from mandi.store import natural_key, read_all_rows, upsert_rows, write_quarantine

FETCHED_AT = "2026-07-08T18:00:00+00:00"


def _rows(cfg, ogd_records):
    rows, _ = normalize_records(cfg, ogd_records, "ogd", FETCHED_AT)
    return rows


def test_upsert_and_read_roundtrip(cfg, ogd_records, tmp_path):
    rows = _rows(cfg, ogd_records)
    changed = upsert_rows(rows, base=tmp_path)
    assert sum(changed.values()) == len(rows)
    stored = read_all_rows(tmp_path)
    assert {natural_key(r) for r in stored} == {natural_key(r) for r in rows}


def test_rerun_is_byte_identical_noop(cfg, ogd_records, tmp_path):
    rows = _rows(cfg, ogd_records)
    upsert_rows(rows, base=tmp_path)
    files = {p: p.read_bytes() for p in tmp_path.glob("*/*.csv")}

    changed = upsert_rows(rows, base=tmp_path)  # identical rerun
    assert changed == {}
    assert {p: p.read_bytes() for p in tmp_path.glob("*/*.csv")} == files


def test_ogd_beats_ceda(cfg, ogd_records, tmp_path):
    rows = _rows(cfg, ogd_records)
    ceda_row = dict(rows[0], source="ceda", modal_price="9999",
                    min_price="9999", max_price="9999")

    upsert_rows([ceda_row], base=tmp_path)
    upsert_rows([rows[0]], base=tmp_path)  # ogd overwrite
    stored = read_all_rows(tmp_path)
    assert len(stored) == 1 and stored[0]["source"] == "ogd"

    upsert_rows([ceda_row], base=tmp_path)  # ceda cannot overwrite back
    stored = read_all_rows(tmp_path)
    assert stored[0]["source"] == "ogd"
    assert stored[0]["modal_price"] == str(rows[0]["modal_price"])


def test_same_source_latest_fetched_at_wins(cfg, ogd_records, tmp_path):
    row = _rows(cfg, ogd_records)[0]
    newer = dict(row, fetched_at="2026-07-09T18:00:00+00:00", modal_price="5150")
    upsert_rows([row], base=tmp_path)
    upsert_rows([newer], base=tmp_path)
    stored = read_all_rows(tmp_path)
    assert len(stored) == 1 and stored[0]["modal_price"] == "5150"

    upsert_rows([row], base=tmp_path)  # older fetch cannot regress it
    assert read_all_rows(tmp_path)[0]["modal_price"] == "5150"


def test_partitioned_by_slug_and_year(cfg, ogd_records, tmp_path):
    rows = _rows(cfg, ogd_records)
    upsert_rows(rows, base=tmp_path)
    partitions = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.glob("*/*.csv"))
    assert partitions == ["arecanut/2026.csv", "black-pepper/2026.csv", "coconut/2026.csv"]


def test_quarantine_written_with_reason(cfg, ogd_records, tmp_path):
    _, quarantined = normalize_records(cfg, ogd_records, "ogd", FETCHED_AT)
    path = write_quarantine(quarantined, base=tmp_path, now=FETCHED_AT)
    assert path is not None and path.exists()
    content = path.read_text()
    assert "bad_date" in content and "min_modal_max_order" in content
