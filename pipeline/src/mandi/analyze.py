"""Seasonality, trend and best sell/buy window analysis.

All metrics are interpretable arithmetic (no ML):

- Daily series per commodity at three levels: market, district, region.
  District/region daily value = median of market modal prices that day
  (robust to a single odd market).
- Seasonal index: for each year with >= MIN_MONTHS_PER_YEAR reported months,
  idx[y][m] = monthly_median[y][m] / median(monthly_median[y][*]).
  seasonal_index[m] = median over years of idx[y][m]  (+ IQR, n_years).
  Normalizing within-year before averaging across years removes
  trend/inflation, so indices from different years are comparable.
- Best sell window: the 2-3 consecutive-month span maximizing the mean
  seasonal index. Best buy window: the minimizing span.
- Trend: 30/90-day rolling medians over observed days, YoY change and a
  12-month OLS slope.
- Current-vs-seasonal signal: current 30-day median vs what the seasonal
  index says is typical for this month.

Every metric carries its support (n_obs, n_years) so consumers can qualify
claims. Series stale for more than STALE_DAYS are flagged, not analyzed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import REPO_ROOT, Config
from .store import read_all_rows

BUILD_DIR = REPO_ROOT / "build"
ANALYSIS_PATH = BUILD_DIR / "analysis.json"

MIN_MONTHS_PER_YEAR = 8  # a year must report this many months to join the seasonal index
MIN_PERIODS_ROLLING = 5
STALE_DAYS = 30
WINDOW_SIZES = (2, 3)
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load_frame(base: Path | None = None) -> pd.DataFrame:
    rows = read_all_rows(base)
    if not rows:
        return pd.DataFrame(columns=["date", "district", "market", "commodity_slug",
                                     "variety", "grade", "modal_price"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("min_price", "max_price", "modal_price"):
        df[col] = pd.to_numeric(df[col])
    return df


def _daily_series(df: pd.DataFrame) -> pd.Series:
    """Collapse rows to one value per day: median across markets/varieties."""
    return df.groupby("date")["modal_price"].median().sort_index()


def _monthly_median(daily: pd.Series) -> pd.DataFrame:
    g = daily.groupby([daily.index.year, daily.index.month])
    out = g.agg(median="median", n_obs="count")
    out.index.names = ["year", "month"]
    return out


def _seasonal_index(monthly: pd.DataFrame) -> dict[str, Any] | None:
    """Detrended seasonal index across years. None if too little history."""
    per_year_idx: dict[int, pd.Series] = {}
    for year, grp in monthly.groupby(level="year"):
        months = grp.droplevel("year")["median"]
        if len(months) >= MIN_MONTHS_PER_YEAR:
            per_year_idx[int(year)] = months / months.median()
    if len(per_year_idx) < 2:
        return None

    idx_frame = pd.DataFrame(per_year_idx)  # rows: month 1..12, cols: years
    index = [round(float(idx_frame.loc[m].median()), 4) if m in idx_frame.index else None
             for m in range(1, 13)]
    iqr = [
        round(float(idx_frame.loc[m].quantile(0.75) - idx_frame.loc[m].quantile(0.25)), 4)
        if m in idx_frame.index and idx_frame.loc[m].count() >= 2 else None
        for m in range(1, 13)
    ]
    n_years = [int(idx_frame.loc[m].count()) if m in idx_frame.index else 0
               for m in range(1, 13)]
    return {"index": index, "iqr": iqr, "n_years": n_years,
            "years_used": sorted(per_year_idx)}


def _best_window(seasonal: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """Best 2-3 consecutive month window by mean seasonal index."""
    index = seasonal["index"]
    best: tuple[float, list[int]] | None = None
    for size in WINDOW_SIZES:
        for start in range(12):
            months = [(start + k) % 12 for k in range(size)]
            vals = [index[m] for m in months]
            if any(v is None for v in vals):
                continue
            score = sum(vals) / size
            if best is None or (mode == "sell" and score > best[0]) or (
                    mode == "buy" and score < best[0]):
                best = (score, months)
    if best is None:
        return None
    score, months = best
    iqrs = [seasonal["iqr"][m] for m in months if seasonal["iqr"][m] is not None]
    years = min(seasonal["n_years"][m] for m in months)
    mean_iqr = sum(iqrs) / len(iqrs) if iqrs else None

    if years >= 5 and mean_iqr is not None and mean_iqr < 0.08:
        confidence = "high"
    elif years >= 3 and mean_iqr is not None and mean_iqr < 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "months": [m + 1 for m in months],
        "month_names": [MONTH_NAMES[m] for m in months],
        "premium_pct": round((score - 1.0) * 100, 1),
        "confidence": confidence,
        "n_years": years,
        "mean_iqr": round(mean_iqr, 4) if mean_iqr is not None else None,
    }


def _trend(daily: pd.Series, today: date) -> dict[str, Any]:
    out: dict[str, Any] = {"ma30": None, "ma90": None, "yoy_pct": None,
                           "slope_pct_per_year": None, "direction": "flat"}
    if daily.empty:
        return out

    def window_median(end: date, days: int) -> float | None:
        start = end - timedelta(days=days)
        vals = daily[(daily.index > pd.Timestamp(start)) & (daily.index <= pd.Timestamp(end))]
        if len(vals) < MIN_PERIODS_ROLLING:
            return None
        return float(vals.median())

    ma30 = window_median(today, 30)
    ma90 = window_median(today, 90)
    out["ma30"] = round(ma30) if ma30 is not None else None
    out["ma90"] = round(ma90) if ma90 is not None else None

    prev_year = window_median(today - timedelta(days=365), 30)
    if ma30 is not None and prev_year:
        out["yoy_pct"] = round((ma30 / prev_year - 1.0) * 100, 1)

    # 12-month OLS slope on monthly medians, as % of level per year
    recent = daily[daily.index > pd.Timestamp(today - timedelta(days=365))]
    monthly = recent.resample("MS").median().dropna()
    if len(monthly) >= 6:
        x = pd.Series(range(len(monthly)), index=monthly.index, dtype=float)
        cov = ((x - x.mean()) * (monthly - monthly.mean())).sum()
        var = ((x - x.mean()) ** 2).sum()
        slope_per_month = cov / var if var else 0.0
        level = float(monthly.median())
        if level > 0:
            pct = slope_per_month * 12 / level * 100
            out["slope_pct_per_year"] = round(pct, 1)
            out["direction"] = "up" if pct > 5 else ("down" if pct < -5 else "flat")
    return out


def _signal(daily: pd.Series, seasonal: dict[str, Any] | None,
            trend: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Current 30d median vs typical for this month: > 0 means above-typical."""
    if seasonal is None or trend["ma30"] is None:
        return None
    month_idx = seasonal["index"][today.month - 1]
    if month_idx is None:
        return None
    year_back = daily[daily.index > pd.Timestamp(today - timedelta(days=365))]
    if len(year_back) < 30:
        return None
    typical = float(year_back.median()) * month_idx
    if typical <= 0:
        return None
    return {
        "month": MONTH_NAMES[today.month - 1],
        "vs_typical_pct": round((trend["ma30"] / typical - 1.0) * 100, 1),
    }


def _narrative(display: str, sell: dict[str, Any] | None,
               buy: dict[str, Any] | None, level_name: str) -> list[str]:
    lines: list[str] = []
    if sell:
        span = f"{sell['month_names'][0]}–{sell['month_names'][-1]}"
        lines.append(
            f"Historically, {display} sells about {abs(sell['premium_pct'])}% "
            f"{'above' if sell['premium_pct'] >= 0 else 'below'} its yearly average in {span} "
            f"in {level_name} ({sell['n_years']} years of data — {sell['confidence']} confidence)."
        )
    if buy:
        span = f"{buy['month_names'][0]}–{buy['month_names'][-1]}"
        lines.append(
            f"Prices tend to be lowest around {span} "
            f"({abs(buy['premium_pct'])}% below yearly average) — "
            f"historically the better time to buy."
        )
    if not lines:
        lines.append(
            f"Not enough multi-year history yet to estimate seasonality for {display} "
            f"in {level_name}. The daily pipeline is accumulating data."
        )
    return lines


def _analyze_series(df: pd.DataFrame, display: str, level_name: str,
                    today: date) -> dict[str, Any] | None:
    if df.empty:
        return None
    daily = _daily_series(df)
    last_observed = daily.index.max().date()
    days_stale = (today - last_observed).days
    stale = days_stale > STALE_DAYS

    monthly = _monthly_median(daily)
    seasonal = _seasonal_index(monthly)
    trend = _trend(daily, today) if not stale else {
        "ma30": None, "ma90": None, "yoy_pct": None,
        "slope_pct_per_year": None, "direction": "flat"}
    sell = _best_window(seasonal, "sell") if seasonal else None
    buy = _best_window(seasonal, "buy") if seasonal else None

    return {
        "level": level_name,
        "n_obs": int(len(daily)),
        "freshness": {
            "last_observed": last_observed.isoformat(),
            "days_stale": days_stale,
            "stale": stale,
        },
        "latest": {
            "date": last_observed.isoformat(),
            "modal_price": round(float(daily.iloc[-1])),
        },
        "trend": trend,
        "signal": _signal(daily, seasonal, trend, today),
        "seasonality": None if seasonal is None else {
            **seasonal,
            "best_sell": sell,
            "best_buy": buy,
        },
        "narrative": _narrative(display, sell, buy, level_name),
        "monthly": [
            {"month": f"{int(y):04d}-{int(m):02d}",
             "median": round(float(row["median"])),
             "n_obs": int(row["n_obs"])}
            for (y, m), row in monthly.iterrows()
        ],
    }


def analyze_all(cfg: Config, base: Path | None = None,
                today: date | None = None) -> dict[str, Any]:
    df = load_frame(base)
    today = today or (df["date"].max().date() if not df.empty else date.today())

    # benchmark districts are comparison references (e.g. Sirsi for arecanut,
    # Kochi for pepper) — analyzed individually but excluded from the home
    # region's pooled series so they don't distort local seasonality
    benchmark_names = {d.name for d in cfg.districts if d.benchmark}

    out: dict[str, Any] = {"today": today.isoformat(), "commodities": {}}
    for c in cfg.commodities:
        cdf = df[df["commodity_slug"] == c.slug] if not df.empty else df
        districts = {}
        if not cdf.empty:
            for dname, ddf in cdf.groupby("district"):
                res = _analyze_series(ddf, c.display, str(dname), today)
                if res:
                    res["benchmark"] = str(dname) in benchmark_names
                    districts[str(dname)] = res
        home = cdf[~cdf["district"].isin(benchmark_names)] if not cdf.empty else cdf
        out["commodities"][c.slug] = {
            "slug": c.slug,
            "display": c.display,
            "unit": c.unit,
            "region": _analyze_series(home, c.display, cfg.region_label, today),
            "districts": districts,
        }
    return out


def write_analysis(results: dict[str, Any], path: Path | None = None) -> Path:
    path = path or ANALYSIS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    return path
