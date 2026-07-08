"""One-time historical backfill from the CEDA (Ashoka University) Agmarknet API.

https://api.ceda.ashoka.edu.in/documentation/
Requires a free CEDA account token in the CEDA_API_TOKEN env var.
CEDA terms: non-commercial use with attribution (kept in README + site footer).

CEDA returns market-level aggregates without variety/grade, so backfilled
rows use variety="Aggregate", grade="CEDA". Where an OGD row already exists
for the same (date, market, commodity), the CEDA row is skipped entirely so
daily medians aren't double-counted.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import requests

from .config import Config
from .store import read_all_rows, upsert_rows, write_quarantine

log = logging.getLogger(__name__)

BASE_URL = "https://api.ceda.ashoka.edu.in/v1"
TIMEOUT_S = 60
SLEEP_S = 1.5
CHUNK_DAYS = 365


class CedaError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("CEDA_API_TOKEN", "").strip()
    if not token:
        raise CedaError(
            "CEDA_API_TOKEN is not set. Register (free) at "
            "https://agmarknet.ceda.ashoka.edu.in/ and create an API token."
        )
    return token


def _call(session: requests.Session, method: str, path: str,
          payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    for attempt in range(1, 4):
        resp = session.request(method, url, json=payload, timeout=TIMEOUT_S)
        if resp.status_code == 429:
            wait = 30 * attempt
            log.warning("CEDA 429 rate-limited; sleeping %ss", wait)
            time.sleep(wait)
            continue
        if resp.status_code == 401:
            raise CedaError("CEDA token rejected (401). Check CEDA_API_TOKEN.")
        resp.raise_for_status()
        return resp.json()
    raise CedaError(f"CEDA rate limit persisted for {path}")


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _match_commodities(cfg: Config, ceda: list[dict[str, Any]]) -> dict[str, int]:
    """Map our slug -> CEDA commodity_id by name."""
    by_norm = {_norm(c["name"]): int(c["id"]) for c in ceda}
    out: dict[str, int] = {}
    for c in cfg.commodities:
        for candidate in (*c.ogd_names, c.display):
            cid = by_norm.get(_norm(candidate))
            if cid is not None:
                out[c.slug] = cid
                break
        else:
            log.warning("no CEDA commodity match for %s (tried %s)", c.slug, c.ogd_names)
    return out


def _match_districts(cfg: Config, geographies: list[dict[str, Any]]
                     ) -> tuple[int, dict[str, int]]:
    """Return (state_id, {canonical district name -> district_id})."""
    state = next(
        (s for s in geographies
         if _norm(s.get("state_name", "")) in {_norm(n) for n in cfg.state_names}),
        None,
    )
    if state is None:
        raise CedaError(f"none of {cfg.state_names} found in CEDA geographies")
    by_norm = {_norm(d["district_name"]): int(d["district_id"])
               for d in state.get("districts", [])}
    out: dict[str, int] = {}
    for d in cfg.districts:
        for candidate in (*d.ogd_names, d.name):
            did = by_norm.get(_norm(candidate.split("(")[0]))
            if did is not None:
                out[d.name] = did
                break
        else:
            log.warning("no CEDA district match for %s", d.name)
    return int(state["state_id"]), out


def backfill(cfg: Config, years: int = 5, dry_run: bool = False,
             today: date | None = None) -> int:
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {_token()}"
    session.headers["User-Agent"] = "malabar-mandi-dashboard (personal non-commercial)"

    commodities = _call(session, "GET", "/agmarknet/commodities")
    slug_to_cid = _match_commodities(
        cfg, commodities.get("commodities") or commodities.get("data") or [])
    time.sleep(SLEEP_S)
    geographies = _call(session, "GET", "/agmarknet/geographies")
    state_id, district_ids = _match_districts(
        cfg, geographies.get("geographies") or geographies.get("data") or [])
    log.info("CEDA ids: state=%s districts=%s commodities=%s",
             state_id, district_ids, slug_to_cid)
    if dry_run:
        print(f"dry-run: state_id={state_id}")
        print(f"dry-run: districts={district_ids}")
        print(f"dry-run: commodities={slug_to_cid}")
        return 0

    today = today or date.today()
    start = today - timedelta(days=365 * years)

    # (date, market, slug) triples already covered by the official OGD feed
    ogd_covered = {
        (r["date"], r["market"], r["commodity_slug"])
        for r in read_all_rows()
        if r["source"] == "ogd"
    }

    fetched_at = today.isoformat() + "T00:00:00+00:00"
    total_rows, quarantined = 0, []
    for c in cfg.commodities:
        cid = slug_to_cid.get(c.slug)
        if cid is None:
            continue
        for dname, did in district_ids.items():
            time.sleep(SLEEP_S)
            markets = _call(session, "POST", "/agmarknet/markets", {
                "commodity_id": cid, "state_id": state_id,
                "district_id": did, "indicator": "price",
            }).get("data") or []
            market_names = {int(m["market_id"]): str(m["market_name"]).strip()
                            for m in markets}
            if not market_names:
                log.info("%s / %s: no CEDA markets", c.slug, dname)
                continue

            chunk_start = start
            rows: list[dict[str, Any]] = []
            while chunk_start <= today:
                chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), today)
                time.sleep(SLEEP_S)
                data = _call(session, "POST", "/agmarknet/prices", {
                    "commodity_id": cid, "state_id": state_id,
                    "district_id": [did], "market_id": sorted(market_names),
                    "from_date": chunk_start.isoformat(),
                    "to_date": chunk_end.isoformat(),
                }).get("data") or []
                for rec in data:
                    market = market_names.get(int(rec.get("market_id", -1)),
                                              f"market#{rec.get('market_id')}")
                    row = {
                        "date": str(rec["date"])[:10],
                        "district": dname,
                        "market": market,
                        "commodity_slug": c.slug,
                        "variety": "Aggregate",
                        "grade": "CEDA",
                        "min_price": int(round(float(rec.get("min_price") or 0))),
                        "max_price": int(round(float(rec.get("max_price") or 0))),
                        "modal_price": int(round(float(rec.get("modal_price") or 0))),
                        "unit": c.unit,
                        "source": "ceda",
                        "fetched_at": fetched_at,
                    }
                    if (row["date"], market, c.slug) in ogd_covered:
                        continue
                    if not (c.sanity_min <= row["modal_price"] <= c.sanity_max):
                        quarantined.append({"reason": "outside_sanity_range",
                                            "source": "ceda", **row})
                        continue
                    if not (row["min_price"] <= row["modal_price"] <= row["max_price"]):
                        # CEDA aggregates occasionally lack min/max; keep modal
                        row["min_price"] = row["max_price"] = row["modal_price"]
                    rows.append(row)
                chunk_start = chunk_end + timedelta(days=1)

            changed = upsert_rows(rows)
            n = sum(changed.values())
            total_rows += n
            log.info("%s / %s: %d rows upserted", c.slug, dname, n)

    write_quarantine(quarantined)
    print(f"backfill complete: {total_rows} rows upserted, "
          f"{len(quarantined)} quarantined")
    return 0
