"""Historical backfill from the Agmarknet 2.0 public report API.

POST https://api.agmarknet.gov.in/v1/daily-price-arrival/report
(the same endpoint the "Daily Price and Arrival Report" page on
agmarknet.gov.in uses; public, no auth). Price data is available from
2021-01-01, max one year per request, paginated.

Rows are converted to the OGD record shape and pushed through the normal
normalize -> quarantine -> upsert path with source="agmarknet".
The OGD daily feed still wins on conflicts (see store.SOURCE_RANK).

Commodity/group IDs come from GET /daily-price-arrival/filters, matched
against the ogd_names in config/sources.yaml — adding a commodity to the
config automatically includes it in future backfills.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

import requests

from .config import Config
from .normalize import normalize_records
from .store import upsert_rows, write_quarantine

log = logging.getLogger(__name__)

BASE_URL = "https://api.agmarknet.gov.in/v1"
TIMEOUT_S = 120
SLEEP_S = 1.5
PAGE_LIMIT = 1000
MAX_RETRIES = 3

# magic "All ..." filter ids used by the report form
ALL_DISTRICTS = 100001
ALL_MARKETS = 100002
ALL_GRADES = 100003
DATA_TYPE_PRICE = 100004
ALL_VARIETIES = 100007

EARLIEST = date(2021, 1, 1)  # API's stated lower bound for price data

USER_AGENT = "malabar-mandi-dashboard (personal non-commercial project)"


class AgmarknetError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Content-Type"] = "application/json"
    return s


def _post(session: requests.Session, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, json=payload, timeout=TIMEOUT_S)
            if resp.status_code == 429:
                time.sleep(30 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < MAX_RETRIES:
                time.sleep(2**attempt)
    raise AgmarknetError(f"POST {path} failed after {MAX_RETRIES} attempts: {last}")


def _get_filters(session: requests.Session) -> dict[str, Any]:
    resp = session.get(f"{BASE_URL}/daily-price-arrival/filters", timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()["data"]


def _match_ids(cfg: Config, filters: dict[str, Any]
               ) -> tuple[dict[str, int], dict[str, tuple[int, int]]]:
    """Return ({canonical state name: state_id}, {slug: (cmdt_id, group_id)})."""
    states = {s["state_name"].strip().lower(): int(s["state_id"])
              for s in filters["state_data"]}
    state_ids: dict[str, int] = {}
    for group in cfg.states:
        sid = next((states[n.lower()] for n in group.names if n.lower() in states), None)
        if sid is None:
            log.warning("state group %s not in agmarknet state list", group.names)
        else:
            state_ids[group.names[0]] = sid

    by_name = {c["cmdt_name"].strip().lower(): (int(c["cmdt_id"]), int(c["cmdt_group_id"]))
               for c in filters["cmdt_data"]}
    ids: dict[str, tuple[int, int]] = {}
    for c in cfg.commodities:
        for name in c.ogd_names:
            hit = by_name.get(name.lower())
            if hit:
                ids[c.slug] = hit
                break
        else:
            log.warning("no agmarknet commodity match for %s", c.slug)
    return state_ids, ids


def _to_ogd_shape(rec: dict[str, Any]) -> dict[str, Any]:
    """Convert an Agmarknet 2.0 report row to the OGD record shape."""
    def num(v: Any) -> str:
        return str(v or "").replace(",", "")

    day = str(rec.get("arrival_date", "")).strip()  # DD-MM-YYYY
    return {
        "state": rec.get("state_name"),
        "district": rec.get("district_name"),
        "market": rec.get("market_name"),
        "commodity": rec.get("cmdt_name"),
        "variety": rec.get("variety_name"),
        "grade": rec.get("grade_name"),
        "arrival_date": day.replace("-", "/"),
        "min_price": num(rec.get("min_price")),
        "max_price": num(rec.get("max_price")),
        "modal_price": num(rec.get("model_price")),
    }


def _fetch_chunk(session: requests.Session, cmdt_id: int, group_id: int,
                 state_id: int, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = {
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "data_type": str(DATA_TYPE_PRICE),
            "group": str(group_id),
            "commodity": str(cmdt_id),
            "state": f"[{state_id}]",
            "district": f"[{ALL_DISTRICTS}]",
            "market": f"[{ALL_MARKETS}]",
            "grade": f"[{ALL_GRADES}]",
            "variety": f"[{ALL_VARIETIES}]",
            "page": str(page),
            "limit": str(PAGE_LIMIT),
        }
        doc = _post(session, "/daily-price-arrival/report", payload)
        recs = (doc.get("data") or {}).get("records") or []
        flat = [r for group in recs for r in group.get("data", [])]
        rows.extend(flat)
        if len(flat) < PAGE_LIMIT:
            return rows
        page += 1
        time.sleep(SLEEP_S)


def backfill(cfg: Config, years: int = 5, dry_run: bool = False,
             today: date | None = None) -> int:
    session = _session()
    filters = _get_filters(session)
    state_ids, ids = _match_ids(cfg, filters)
    log.info("agmarknet ids: states=%s commodities=%s", state_ids, ids)
    if dry_run:
        print(f"dry-run: state ids={state_ids}")
        print(f"dry-run: commodity ids={ids}")
        return 0

    today = today or date.today()
    start = max(today - timedelta(days=365 * years), EARLIEST)
    fetched_at = f"{today.isoformat()}T00:00:00+00:00"

    total_changed, all_quarantined, skipped = 0, [], []
    for state_name, state_id in state_ids.items():
        for slug, (cmdt_id, group_id) in ids.items():
            chunk_start = start
            while chunk_start <= today:
                chunk_end = min(chunk_start + timedelta(days=364), today)
                try:
                    raw = _fetch_chunk(session, cmdt_id, group_id, state_id,
                                       chunk_start, chunk_end)
                except AgmarknetError as e:
                    # the API 404s on some specific chunks server-side;
                    # skip and report rather than abort the whole backfill
                    log.warning("SKIPPED %s / %s %s..%s: %s", state_name, slug,
                                chunk_start, chunk_end, e)
                    skipped.append(f"{state_name}/{slug} {chunk_start}..{chunk_end}")
                    chunk_start = chunk_end + timedelta(days=1)
                    time.sleep(SLEEP_S)
                    continue
                shaped = [_to_ogd_shape(r) for r in raw]
                rows, quarantined = normalize_records(
                    cfg, shaped, source="agmarknet", fetched_at=fetched_at)
                changed = upsert_rows(rows)
                n = sum(changed.values())
                total_changed += n
                all_quarantined.extend(quarantined)
                log.info("%s / %s %s..%s: %d raw -> %d in-scope, %d changed, "
                         "%d quarantined", state_name, slug, chunk_start,
                         chunk_end, len(raw), len(rows), n, len(quarantined))
                chunk_start = chunk_end + timedelta(days=1)
                time.sleep(SLEEP_S)

    write_quarantine(all_quarantined)
    print(f"agmarknet backfill complete: {total_changed} rows changed, "
          f"{len(all_quarantined)} quarantined")
    if skipped:
        print(f"WARNING: {len(skipped)} chunk(s) skipped (API errors) — "
              f"re-run backfill later to retry: {', '.join(skipped)}")
    return 0
