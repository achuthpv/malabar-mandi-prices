"""Commodity news headlines via Google News RSS (no API key needed).

`python -m mandi news` fetches recent headlines per commodity (query:
"<display> price India", overridable with `news_query` in sources.yaml),
dedupes them into data/news/{slug}.csv, and prunes entries older than
RETENTION_DAYS. The publish step exposes the freshest headlines at
/api/v1/news/{slug}.json so the assistant (and a future model) can cite
what may be moving the market — turning "why did prices change?" answers
from calendar-only to news-aware.

Headlines are UNTRUSTED input: they are stored/served verbatim but the
frontend renders them via textContent only, and URLs are validated to be
http(s) before publishing.
"""

from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from defusedxml import ElementTree

from .config import DATA_DIR, Config

log = logging.getLogger(__name__)

NEWS_DIR = DATA_DIR / "news"
RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
TIMEOUT_S = 30
SLEEP_S = 1.0
RETENTION_DAYS = 120
MAX_ROWS = 400  # per commodity, newest kept
COLUMNS = ["date", "title", "source", "url", "fetched_at"]
USER_AGENT = "malabar-mandi-dashboard (personal non-commercial project)"


def _query(cfg_commodity: Any) -> str:
    return getattr(cfg_commodity, "news_query", "") or \
        f"{cfg_commodity.display} price India"


def fetch_feed(query: str, session: requests.Session) -> list[dict[str, str]]:
    """One RSS fetch -> [{date, title, source, url}]. Failures return []."""
    url = RSS_URL.format(query=quote(query))
    try:
        resp = session.get(url, timeout=TIMEOUT_S)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except (requests.RequestException, ElementTree.ParseError) as e:
        log.warning("news fetch failed for %r: %s", query, e)
        return []

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        try:
            day = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            day = datetime.now(timezone.utc).date().isoformat()
        items.append({"date": day, "title": title, "source": source, "url": link})
    return items


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def merge(existing: list[dict[str, str]], fresh: list[dict[str, str]],
          fetched_at: str, today: str) -> list[dict[str, str]]:
    """Dedup by normalized title, prune old, newest first, capped."""
    cutoff = (datetime.fromisoformat(today)
              - timedelta(days=RETENTION_DAYS)).date().isoformat()
    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    for row in fresh + existing:  # fresh first so re-seen titles keep new date
        key = " ".join(str(row.get("title", "")).lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        if str(row.get("date", "")) < cutoff:
            continue
        merged.append({
            "date": str(row.get("date", "")),
            "title": str(row.get("title", "")),
            "source": str(row.get("source", "")),
            "url": str(row.get("url", "")),
            "fetched_at": str(row.get("fetched_at") or fetched_at),
        })
    merged.sort(key=lambda r: r["date"], reverse=True)
    return merged[:MAX_ROWS]


def fetch(cfg: Config, base: Path | None = None) -> int:
    base = base or NEWS_DIR
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = fetched_at[:10]

    total_new = 0
    for c in cfg.commodities:
        fresh = fetch_feed(_query(c), session)
        path = base / f"{c.slug}.csv"
        existing = _read(path)
        before = {" ".join(r["title"].lower().split()) for r in existing}
        merged = merge(existing, fresh, fetched_at, today)
        new = sum(1 for r in merged
                  if " ".join(r["title"].lower().split()) not in before)
        total_new += new
        _write(path, merged)
        log.info("news %s: %d fetched, %d new, %d kept",
                 c.slug, len(fresh), new, len(merged))
        time.sleep(SLEEP_S)

    print(f"news: {total_new} new headlines across {len(cfg.commodities)} commodities")
    return 0
