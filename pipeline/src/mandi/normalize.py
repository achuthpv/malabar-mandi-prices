"""Normalize raw OGD records into canonical rows; sanity-check; quarantine.

A canonical row is a dict with the columns of data/prices/{slug}/{year}.csv:
date, district, market, commodity_slug, variety, grade,
min_price, max_price, modal_price, unit, source, fetched_at
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .config import Config

log = logging.getLogger(__name__)

COLUMNS = [
    "date",
    "district",
    "market",
    "commodity_slug",
    "variety",
    "grade",
    "min_price",
    "max_price",
    "modal_price",
    "unit",
    "source",
    "fetched_at",
]

EXPECTED_FIELDS = {
    "state",
    "district",
    "market",
    "commodity",
    "variety",
    "grade",
    "arrival_date",
    "min_price",
    "max_price",
    "modal_price",
}

_warned_unknown_fields: set[str] = set()


def parse_arrival_date(value: str) -> date:
    """OGD uses DD/MM/YYYY; be tolerant of ISO too."""
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable arrival_date: {value!r}")


def _clean(s: Any) -> str:
    return " ".join(str(s or "").split())


def _price(v: Any) -> int:
    return int(round(float(v)))


def normalize_records(
    cfg: Config,
    raw_records: list[dict[str, Any]],
    source: str,
    fetched_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (rows, quarantined).

    Rows not matching a configured district+commodity are silently skipped
    (they are out of scope, not bad data). Matching rows that fail parsing
    or sanity checks are quarantined with a reason.
    """
    rows: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []

    for rec in raw_records:
        _warn_unknown_fields(rec)

        district = cfg.district_by_ogd_name.get(_clean(rec.get("district")).lower())
        commodity = cfg.commodity_by_ogd_name.get(_clean(rec.get("commodity")).lower())
        if district is None or commodity is None:
            continue  # out of scope
        # guard against same-named districts in other states
        state = _clean(rec.get("state")).lower()
        if state and state not in district.state_aliases:
            continue
        market = _clean(rec.get("market"))
        if not district.accepts_market(market):
            continue  # not on this district's market whitelist

        def reject(reason: str, rec: dict[str, Any] = rec) -> None:
            quarantined.append({"reason": reason, "source": source, **rec})

        try:
            day = parse_arrival_date(rec.get("arrival_date", ""))
        except ValueError:
            reject("bad_date")
            continue
        if day > datetime.fromisoformat(fetched_at).date():
            reject("future_date")
            continue

        try:
            min_p = _price(rec.get("min_price"))
            max_p = _price(rec.get("max_price"))
            modal_p = _price(rec.get("modal_price"))
        except (TypeError, ValueError):
            reject("bad_price")
            continue

        if modal_p <= 0:
            reject("nonpositive_modal")
            continue
        if not (min_p <= modal_p <= max_p):
            reject("min_modal_max_order")
            continue
        if not (commodity.sanity_min <= modal_p <= commodity.sanity_max):
            reject(f"outside_sanity_range_{commodity.sanity_min}_{commodity.sanity_max}")
            continue

        rows.append(
            {
                "date": day.isoformat(),
                "district": district.name,
                "market": market,
                "commodity_slug": commodity.slug,
                "variety": _clean(rec.get("variety")) or "Other",
                "grade": _clean(rec.get("grade")) or "FAQ",
                "min_price": min_p,
                "max_price": max_p,
                "modal_price": modal_p,
                "unit": commodity.unit,
                "source": source,
                "fetched_at": fetched_at,
            }
        )

    return rows, quarantined


def _warn_unknown_fields(rec: dict[str, Any]) -> None:
    """Log schema drift once per unknown field name (early warning)."""
    for k in rec:
        if k not in EXPECTED_FIELDS and k not in _warned_unknown_fields:
            _warned_unknown_fields.add(k)
            log.warning("unknown field in OGD record (schema drift?): %r", k)
