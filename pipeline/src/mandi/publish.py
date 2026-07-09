"""Generate the static JSON API under site/api/v1/.

The published files ARE the public API (GitHub Pages serves them with
`Access-Control-Allow-Origin: *`), described by site/openapi.json.
Every payload carries generated_at + attribution.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .analyze import ANALYSIS_PATH, analyze_all
from .config import API_DIR, Config
from .store import read_all_rows

ATTRIBUTION = {
    "data_source": "Agmarknet (Directorate of Marketing & Inspection, Govt. of India) "
    "via data.gov.in Open Government Data Platform",
    "historical_source": "CEDA, Ashoka University (agmarknet.ceda.ashoka.edu.in) — "
    "used non-commercially with attribution",
    "license_note": "Personal, non-commercial project. Prices are wholesale mandi "
    "prices in Rs per quintal unless stated otherwise.",
}


def _envelope(generated_at: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"generated_at": generated_at, "attribution": ATTRIBUTION, **payload}


def _write(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return path


def _load_analysis(cfg: Config) -> dict[str, Any]:
    if ANALYSIS_PATH.exists():
        with open(ANALYSIS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return analyze_all(cfg)


def publish_all(cfg: Config, generated_at: str, api_dir: Path | None = None,
                data_base: Path | None = None,
                analysis: dict[str, Any] | None = None) -> list[Path]:
    api_dir = api_dir or API_DIR
    analysis = analysis or _load_analysis(cfg)
    rows = read_all_rows(data_base)
    written: list[Path] = []

    dates = sorted(r["date"] for r in rows) if rows else []

    # --- meta + commodities + markets -------------------------------------
    written.append(_write(api_dir / "meta.json", _envelope(generated_at, {
        "region": cfg.region_label,
        "districts": [d.name for d in cfg.districts],
        "date_range": {"first": dates[0] if dates else None,
                       "last": dates[-1] if dates else None},
        "row_count": len(rows),
        "analysis_as_of": analysis.get("today"),
    })))

    written.append(_write(api_dir / "commodities.json", _envelope(generated_at, {
        "commodities": [
            {"slug": c.slug, "display": c.display, "unit": c.unit,
             "ogd_names": list(c.ogd_names)}
            for c in cfg.commodities
        ],
    })))

    markets: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["district"], r["market"])
        m = markets.setdefault(key, {"district": key[0], "market": key[1],
                                     "first_observed": r["date"],
                                     "last_observed": r["date"],
                                     "commodities": set()})
        m["first_observed"] = min(m["first_observed"], r["date"])
        m["last_observed"] = max(m["last_observed"], r["date"])
        m["commodities"].add(r["commodity_slug"])
    written.append(_write(api_dir / "markets.json", _envelope(generated_at, {
        "markets": [
            {**m, "commodities": sorted(m["commodities"])}
            for _, m in sorted(markets.items())
        ],
    })))

    # --- per-commodity price + analysis files ------------------------------
    rows_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        rows_by_slug[r["commodity_slug"]].append(r)

    for c in cfg.commodities:
        crows = sorted(rows_by_slug.get(c.slug, []),
                       key=lambda r: (r["date"], r["district"], r["market"]))
        canal = analysis["commodities"].get(c.slug, {})

        # latest.json: newest row per market×variety + district latest medians
        latest_by_market: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in crows:  # crows sorted by date => last write wins
            latest_by_market[(r["district"], r["market"], r["variety"])] = r
        spread = _spread(latest_by_market)
        varieties = _variety_summary(crows)
        district_latest = {
            dname: {"date": d["latest"]["date"],
                    "modal_price": d["latest"]["modal_price"],
                    "days_stale": d["freshness"]["days_stale"]}
            for dname, d in canal.get("districts", {}).items()
        }
        written.append(_write(api_dir / "prices" / c.slug / "latest.json",
                              _envelope(generated_at, {
            "commodity": c.slug,
            "unit": c.unit,
            "markets": [
                {"district": r["district"], "market": r["market"],
                 "date": r["date"], "variety": r["variety"], "grade": r["grade"],
                 "min_price": int(r["min_price"]), "max_price": int(r["max_price"]),
                 "modal_price": int(r["modal_price"])}
                for _, r in sorted(latest_by_market.items())
            ],
            "districts": district_latest,
            "spread": spread,
            "varieties": varieties,
        })))

        # daily/{year}.json
        by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in crows:
            by_year[r["date"][:4]].append(r)
        years = sorted(by_year)
        for year, yrows in by_year.items():
            written.append(_write(api_dir / "prices" / c.slug / "daily" / f"{year}.json",
                                  _envelope(generated_at, {
                "commodity": c.slug, "year": int(year), "unit": c.unit,
                "columns": ["date", "district", "market", "variety", "grade",
                            "min_price", "max_price", "modal_price"],
                "rows": [
                    [r["date"], r["district"], r["market"], r["variety"], r["grade"],
                     int(r["min_price"]), int(r["max_price"]), int(r["modal_price"])]
                    for r in yrows
                ],
            })))

        # monthly.json: region + district monthly medians (compact history)
        region = canal.get("region")
        written.append(_write(api_dir / "prices" / c.slug / "monthly.json",
                              _envelope(generated_at, {
            "commodity": c.slug, "unit": c.unit,
            "years_available": [int(y) for y in years],
            "region": (region or {}).get("monthly", []),
            "districts": {
                dname: d.get("monthly", [])
                for dname, d in canal.get("districts", {}).items()
            },
        })))

        # analysis files
        seasonality = (region or {}).get("seasonality")
        written.append(_write(api_dir / "analysis" / c.slug / "seasonality.json",
                              _envelope(generated_at, {
            "commodity": c.slug,
            "level": cfg.region_label,
            "seasonality": seasonality,
            "districts": {
                dname: d.get("seasonality")
                for dname, d in canal.get("districts", {}).items()
            },
        })))

        written.append(_write(api_dir / "analysis" / c.slug / "summary.json",
                              _envelope(generated_at, {
            "commodity": c.slug,
            "display": c.display,
            "unit": c.unit,
            "region": _summary_view(region),
            "districts": {dname: _summary_view(d)
                          for dname, d in canal.get("districts", {}).items()},
            "spread": spread,
        })))

    # --- discovery index ----------------------------------------------------
    endpoints = ["/api/v1/meta.json", "/api/v1/commodities.json", "/api/v1/markets.json"]
    for c in cfg.commodities:
        endpoints += [f"/api/v1/prices/{c.slug}/latest.json",
                      f"/api/v1/prices/{c.slug}/monthly.json"]
        endpoints += [f"/api/v1/prices/{c.slug}/daily/{y}.json"
                      for y in sorted({r['date'][:4] for r in rows_by_slug.get(c.slug, [])})]
        endpoints += [f"/api/v1/analysis/{c.slug}/seasonality.json",
                      f"/api/v1/analysis/{c.slug}/summary.json"]
    written.insert(0, _write(api_dir / "index.json", _envelope(generated_at, {
        "endpoints": endpoints,
        "openapi": "/openapi.json",
    })))

    return written


def _summary_view(series: dict[str, Any] | None) -> dict[str, Any] | None:
    """Everything an LLM needs in one call, without the bulky monthly table."""
    if not series:
        return None
    seasonality = series.get("seasonality") or {}
    return {
        "level": series["level"],
        "benchmark": bool(series.get("benchmark", False)),
        "latest": series["latest"],
        "freshness": series["freshness"],
        "trend": series["trend"],
        "signal": series["signal"],
        "best_sell": seasonality.get("best_sell"),
        "best_buy": seasonality.get("best_buy"),
        "narrative": series["narrative"],
        "n_obs": series["n_obs"],
    }


def _recent_rows(rows: list[dict[str, Any]], window_days: int
                 ) -> list[dict[str, Any]]:
    """Rows within window_days of the newest observation.

    Anchoring on the newest row (not today) keeps summaries meaningful
    when a feed pauses for a few days. Shared by the spread and the
    variety summary so 'recent' means the same thing everywhere.
    """
    from datetime import date, timedelta

    if not rows:
        return []
    newest = max(date.fromisoformat(r["date"]) for r in rows)
    cutoff = (newest - timedelta(days=window_days)).isoformat()
    return [r for r in rows if r["date"] >= cutoff]


def _variety_summary(crows: list[dict[str, Any]], window_days: int = 30
                     ) -> list[dict[str, Any]]:
    """Recent price summary per variety (e.g. Rashi vs Chali vs Hale Chali)."""
    from statistics import median

    by_variety: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _recent_rows(crows, window_days):
        by_variety[r["variety"]].append(r)

    out = []
    for variety, rows in by_variety.items():
        prices = [int(r["modal_price"]) for r in rows]
        out.append({
            "variety": variety,
            "median_modal": round(median(prices)),
            "min_modal": min(prices),
            "max_modal": max(prices),
            "n_obs": len(rows),
            "n_markets": len({(r["district"], r["market"]) for r in rows}),
            "last_observed": max(r["date"] for r in rows),
        })
    out.sort(key=lambda v: -v["median_modal"])
    return out


def _spread(latest_by_market: dict[tuple, dict[str, Any]],
            window_days: int = 7, band: float = 2.0) -> dict[str, Any] | None:
    """Current cross-market price gap: highest vs lowest recent MARKET price.

    Input rows are one per (district, market, variety); they are collapsed
    to one representative price per market (median across that market's
    varieties) BEFORE comparing, so two varieties of one town can never be
    presented as a cross-market gap and n_markets counts real markets.

    Two comparability guards, because a naive max-vs-min is misleading:
    - staleness: only markets that reported within window_days of the newest
      observation count — a stale price is not an arbitrage opportunity;
    - variety/product outliers: markets priced outside [median/band,
      median*band] of the cross-market median are excluded (e.g. a market
      quoting only cheap fresh-form arecanut is a different product, not a
      spread).
    """
    from statistics import median

    recent = _recent_rows(list(latest_by_market.values()), window_days)
    if len(recent) < 2:
        return None

    # one representative price per market: median across its variety rows
    by_market: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in recent:
        by_market[(r["district"], r["market"])].append(r)
    markets = []
    for (district, market), rows in by_market.items():
        prices = sorted(int(r["modal_price"]) for r in rows)
        markets.append({
            "district": district,
            "market": market,
            "modal_price": prices[len(prices) // 2],
            "date": max(r["date"] for r in rows),
        })
    if len(markets) < 2:
        return None

    med = median(m["modal_price"] for m in markets)
    comparable = [m for m in markets
                  if med / band <= m["modal_price"] <= med * band]
    if len(comparable) < 2:
        return None
    lo = min(comparable, key=lambda m: m["modal_price"])
    hi = max(comparable, key=lambda m: m["modal_price"])
    if lo["modal_price"] <= 0 or hi is lo:
        return None
    return {
        "as_of": max(m["date"] for m in markets),
        "window_days": window_days,
        "n_markets": len(comparable),
        "n_excluded": len(markets) - len(comparable),
        "high": hi,
        "low": lo,
        "spread_pct": round((hi["modal_price"] / lo["modal_price"] - 1.0) * 100, 1),
    }
