"""Kerala DES (Directorate of Economics & Statistics) daily wholesale prices.

Source: the "Daily Market Wholesale Price" PDF bulletin published every
working day at https://www.ecostat.kerala.gov.in/storage/daily-data/{id}.pdf
and listed on https://www.ecostat.kerala.gov.in/daily-data (an Inertia.js
page whose embedded JSON gives each upload's id, date and type — the price
bulletin rows have value "MI PRICE").

This is the ONLY machine-readable source of Kozhikode-town prices we found:
Agmarknet has almost no Kozhikode coverage (Kerala has no APMC system), but
DES surveys every district HQ daily and distinguishes arecanut forms
(Dry New vs Dry Old vs Ripe vs Tender) and five pepper types.

The upload id space is shared with other DES publications (e.g. "Rubber
Timber Price"), so every PDF's header is verified before parsing.

Incremental state lives in data/des_state.json (committed by the daily
cron): {"last_id": N}. `des-fetch` ingests everything newer than last_id up
to the latest listed bulletin — so a few days of cron downtime heal
automatically on the next run.
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from pypdf import PdfReader

from .config import DATA_DIR, Config
from .store import upsert_rows, write_quarantine

log = logging.getLogger(__name__)

BASE = "https://www.ecostat.kerala.gov.in"
STATE_PATH = DATA_DIR / "des_state.json"
TIMEOUT_S = 60
SLEEP_S = 0.5
MAX_RETRIES = 3
FIRST_KNOWN_ID = 457  # ~2022-09-27, earliest bulletin we verified
USER_AGENT = "malabar-mandi-dashboard (personal non-commercial project)"

HEADER_RE = re.compile(r"DAILY MARKET WHOLESALE PRICE\s*:\s*(\d{2}-\d{2}-\d{4})")
# Item headings are "<Name> - <Unit>". Names may contain digits ("Rubber
# RSS 4") and units vary wildly ("Quintal", "1000 Nos", "One Box",
# "4.54 Kg") — an unrecognized heading would leak the following table's
# rows into the previous item, so match ANY short unit; only unit=="Quintal"
# items are ingested downstream. The unit class excludes '-' so date lines
# like "Price on - 08-07-2026" never match.
ITEM_RE = re.compile(r"^([A-Z][A-Za-z0-9 ()'./&-]+?)\s+-\s+([A-Za-z0-9. ]{1,20})\s*$")
PRICE_RE = re.compile(r"^-?\d{1,7}\.\d{1,2}$")
SLNO_RE = re.compile(r"^\d{1,4}$")
# page-continuation header fragments that must not be mistaken for markets
NOISE_RE = re.compile(
    r"^(Sl|No|Item|Variation|Price on\b.*|DAILY MARKET WHOLESALE PRICE.*|"
    r"Directorate Of Economics.*|Market Intelligence Section.*|Page \d+.*)$",
    re.IGNORECASE,
)

# DES item heading -> (commodity slug, variety). Quintal items only; the
# per-1000-nuts quotes and oil/copra products are different units/products.
ITEM_MAP = {
    "Arecanut (Ripe)": ("arecanut", "Ripe"),
    "Tender Arecanut (Paiga)": ("arecanut", "Tender (Paiga)"),
    "Arecanut Dry 1st Quality": ("arecanut", "Dry 1st Quality"),
    "Arecanut Dry 2nd Quality": ("arecanut", "Dry 2nd Quality"),
    "Arecanut Dry New": ("arecanut", "Dry New"),
    "Arecanut Dry Old": ("arecanut", "Dry Old"),
    "Pepper (Nadan)": ("black-pepper", "Nadan"),
    "Pepper Garbled": ("black-pepper", "Garbled"),
    "Pepper (Ungarbled)": ("black-pepper", "Ungarbled"),
    "Pepper (Wayanadan)": ("black-pepper", "Wayanadan"),
    "Pepper (Chettan)": ("black-pepper", "Chettan"),
    "Coconut Without Husk (W O H)": ("coconut", "Without Husk"),
}


class DesError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get(session: requests.Session, url: str) -> requests.Response | None:
    """GET with retries; returns None on 404."""
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT_S)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last = e
            if attempt < MAX_RETRIES:
                time.sleep(2**attempt)
    raise DesError(f"GET {url} failed after {MAX_RETRIES} attempts: {last}")


def discover_bulletins(session: requests.Session) -> list[tuple[int, str]]:
    """Latest MI PRICE bulletins from the /daily-data listing: [(id, date)]."""
    resp = _get(session, f"{BASE}/daily-data")
    if resp is None:
        return []
    m = re.search(r'data-page="([^"]+)"', resp.text)
    if not m:
        log.warning("DES /daily-data page had no Inertia data-page attribute")
        return []
    try:
        props = json.loads(html.unescape(m.group(1))).get("props", {})
    except ValueError:
        log.warning("DES /daily-data data-page JSON did not parse")
        return []
    out = []
    for entry in props.get("data") or []:
        if entry.get("value") == "MI PRICE" and entry.get("file"):
            out.append((int(entry["id"]), str(entry.get("date") or "")))
    return sorted(out)


# --------------------------------------------------------------------------
# PDF parsing
# --------------------------------------------------------------------------

def parse_bulletin_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse extracted PDF text into (iso_date, entries).

    Entry: {"item", "unit", "market", "price"} — price is the day's value.
    The text is a line stream: item headings ("<Item> - <Unit>"), then per
    market: serial number, market name, then usually three decimals
    (previous price, current price, variation). Markets with no report
    have no decimals at all.
    """
    m = HEADER_RE.search(text)
    if not m:
        raise DesError("not a DAILY MARKET WHOLESALE PRICE bulletin")
    date = datetime.strptime(m.group(1), "%d-%m-%Y").date().isoformat()

    entries: list[dict[str, Any]] = []
    item = unit = None
    market: str | None = None
    prices: list[float] = []

    def flush() -> None:
        nonlocal market, prices
        if item and market and prices:
            # 3 decimals = (prev, current, variation); fewer = last is current
            price = prices[1] if len(prices) >= 2 else prices[0]
            entries.append({"item": item, "unit": unit,
                            "market": market, "price": price})
        market, prices = None, []

    for raw in text.split("\n"):
        line = " ".join(raw.split())
        if not line:
            continue
        im = ITEM_RE.match(line)
        if im and im.group(1).strip() != "Sl No":
            flush()
            item, unit = im.group(1).strip(), im.group(2)
            continue
        if item is None:
            continue
        if NOISE_RE.match(line):
            # page-break header: skip WITHOUT flushing, so an entry whose
            # name and prices straddle the page boundary still completes
            continue
        if SLNO_RE.match(line):
            flush()
            continue
        if PRICE_RE.match(line):
            if market:
                prices.append(float(line))
            continue
        # non-numeric, not a heading: market name (may wrap onto two lines)
        if market and not prices:
            market += " " + line
        elif not market:
            market = line
        else:
            flush()
            market = line
    flush()
    return date, entries


def _to_rows(cfg: Config, date: str, entries: list[dict[str, Any]],
             fetched_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, quarantined = [], []
    for e in entries:
        if e["unit"] != "Quintal":
            continue
        mapped = ITEM_MAP.get(e["item"])
        if not mapped:
            continue
        slug, variety = mapped
        town = e["market"].strip()
        district = cfg.district_by_des_market.get(town.lower()) \
            or cfg.district_by_des_market.get(town.split(" (")[0].lower())
        if district is None:
            continue  # town not configured — out of scope
        commodity = cfg.commodity(slug)
        price = int(round(e["price"]))
        row = {
            "date": date,
            "district": district.name,
            "market": town,
            "commodity_slug": slug,
            "variety": variety,
            "grade": "DES",
            "min_price": price,
            "max_price": price,
            "modal_price": price,
            "unit": commodity.unit,
            "source": "des",
            "fetched_at": fetched_at,
        }
        if price <= 0:
            quarantined.append({"reason": "nonpositive_modal", "source": "des", **row})
        elif not (commodity.sanity_min <= price <= commodity.sanity_max):
            quarantined.append({"reason": "outside_sanity_range", "source": "des", **row})
        else:
            rows.append(row)
    return rows, quarantined


# --------------------------------------------------------------------------
# state + ingestion
# --------------------------------------------------------------------------

def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"last_id": FIRST_KNOWN_ID - 1}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write("\n")


def ingest_id(cfg: Config, session: requests.Session, pdf_id: int,
              fetched_at: str) -> tuple[int, int] | None:
    """Fetch + ingest one upload id. None if missing/not a price bulletin."""
    resp = _get(session, f"{BASE}/storage/daily-data/{pdf_id}.pdf")
    if resp is None:
        return None
    try:
        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join(page.extract_text() for page in reader.pages)
        date, entries = parse_bulletin_text(text)
    except DesError:
        return None  # some other DES publication sharing the id space
    except Exception as e:  # noqa: BLE001 — malformed PDF: skip, don't abort
        log.warning("DES id %d: unreadable PDF (%s)", pdf_id, e)
        return None

    rows, quarantined = _to_rows(cfg, date, entries, fetched_at)
    changed = sum(upsert_rows(rows).values())
    write_quarantine(quarantined)
    log.info("DES id %d (%s): %d entries -> %d in-scope, %d changed, %d quarantined",
             pdf_id, date, len(entries), len(rows), changed, len(quarantined))
    return changed, len(rows)


def fetch(cfg: Config) -> int:
    """Cron entrypoint: ingest every bulletin newer than the saved state."""
    session = _session()
    state = _load_state()
    last_id = int(state.get("last_id", FIRST_KNOWN_ID - 1))

    listed = discover_bulletins(session)
    newest_listed = max((i for i, _ in listed), default=None)
    if newest_listed is None:
        # listing unreachable — probe a small window forward instead
        newest_listed = last_id + 15
        log.warning("DES listing unavailable; probing ids %d..%d",
                    last_id + 1, newest_listed)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_changed = ingested = 0
    for pdf_id in range(last_id + 1, newest_listed + 1):
        result = ingest_id(cfg, session, pdf_id, fetched_at)
        if result:
            total_changed += result[0]
            ingested += 1
        time.sleep(SLEEP_S)

    if newest_listed > last_id:
        state["last_id"] = newest_listed
        _save_state(state)
    print(f"des-fetch: ids {last_id + 1}..{newest_listed}, "
          f"{ingested} bulletins ingested, {total_changed} rows changed")
    if ingested and total_changed == 0:
        log.warning("bulletins parsed but produced no in-scope rows — "
                    "check ITEM_MAP/des_markets for drift")
    return 0


def backfill(cfg: Config, start_id: int | None = None,
             end_id: int | None = None) -> int:
    """One-time historical sweep over the upload id range."""
    session = _session()
    start_id = start_id or FIRST_KNOWN_ID
    if end_id is None:
        listed = discover_bulletins(session)
        end_id = max((i for i, _ in listed), default=None)
        if end_id is None:
            raise DesError("could not discover the latest bulletin id; pass --end-id")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_changed = bulletins = 0
    for pdf_id in range(start_id, end_id + 1):
        result = ingest_id(cfg, session, pdf_id, fetched_at)
        if result:
            total_changed += result[0]
            bulletins += 1
        time.sleep(SLEEP_S)

    state = _load_state()
    if end_id > int(state.get("last_id", 0)):
        state["last_id"] = end_id
        _save_state(state)
    print(f"des-backfill complete: ids {start_id}..{end_id}, "
          f"{bulletins} bulletins, {total_changed} rows changed")
    return 0
