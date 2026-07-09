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

Mapping is config-driven (config/sources.yaml): districts declare
`des_markets` town names and commodities declare `des_items` heading ->
variety mappings, so adding a commodity or town needs no code change.
Parsed entries are reshaped into OGD record form and pushed through
normalize.normalize_records — the same validation/quarantine path every
other source uses.

The upload id space is shared with other DES publications (e.g. "Rubber
Timber Price"), so every PDF's header is verified before parsing.

Incremental state lives in data/des_state.json (committed by the daily
cron): {"last_id": N}. When the /daily-data listing is reachable its newest
bulletin id is authoritative (ids below it are already allocated and can
never become bulletins later); when it is not, we probe forward but only
advance the state past ids that actually EXISTED — an id that 404s today
may be published tomorrow and must be revisited.
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
from .normalize import normalize_records
from .store import upsert_rows, write_quarantine

log = logging.getLogger(__name__)

BASE = "https://www.ecostat.kerala.gov.in"
STATE_PATH = DATA_DIR / "des_state.json"
TIMEOUT_S = 60
SLEEP_S = 0.5
MAX_RETRIES = 3
PROBE_WINDOW = 15  # forward-probe width when the listing page is down
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


class DesError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get(session: requests.Session, url: str) -> requests.Response | None:
    """GET with retries and 429 backoff; returns None on 404."""
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT_S)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = 30 * attempt
                log.warning("DES 429 rate-limited; sleeping %ss", wait)
                time.sleep(wait)
                continue
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

    def current_price(vals: list[float]) -> float:
        """Pick the day's price from a decimals group.

        3 values = (prev, current, variation). 2 values are ambiguous:
        (prev, current) when the variation column is blank, but
        (current, variation) when the previous-day column is blank (e.g. a
        newly surveyed market). Disambiguate by magnitude: a variation is
        small relative to a price, and prev/current are the same scale.
        """
        if len(vals) >= 3:
            return vals[1]
        if len(vals) == 2:
            hi = max(abs(vals[0]), abs(vals[1]))
            if hi > 0 and abs(vals[1]) <= 0.5 * hi and abs(vals[0]) > abs(vals[1]):
                return vals[0]  # (current, variation)
            return vals[1]  # (prev, current)
        return vals[0]

    def flush() -> None:
        nonlocal market, prices
        if item and market and prices:
            entries.append({"item": item, "unit": unit,
                            "market": market, "price": current_price(prices)})
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


def _to_ogd_shape(cfg: Config, date: str,
                  entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reshape in-scope bulletin entries into OGD record form so they flow
    through normalize.normalize_records like every other source."""
    shaped = []
    for e in entries:
        if e["unit"] != "Quintal":
            continue
        mapped = cfg.des_item_map.get(e["item"])
        if not mapped:
            continue  # item not configured for any commodity — out of scope
        commodity, variety = mapped
        town = e["market"].strip()
        district = cfg.district_by_des_market.get(town.lower()) \
            or cfg.district_by_des_market.get(town.split(" (")[0].lower())
        if district is None:
            continue  # town not configured — out of scope
        price = e["price"]
        shaped.append({
            "state": district.state_aliases[0],
            "district": district.ogd_names[0],
            "market": town,
            "commodity": commodity.ogd_names[0],
            "variety": variety,
            "grade": "DES",
            "arrival_date": date,  # ISO accepted by parse_arrival_date
            "min_price": price,
            "max_price": price,
            "modal_price": price,
        })
    return shaped


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


def _read_bulletin(session: requests.Session, pdf_id: int
                   ) -> tuple[str, tuple[str, list[dict[str, Any]]] | None]:
    """Fetch one upload id. Returns (status, payload):
    ("missing", None)  — 404: nothing at this id (may be published later)
    ("other", None)    — exists but is another DES publication / unreadable
    ("ok", (date, entries))
    """
    resp = _get(session, f"{BASE}/storage/daily-data/{pdf_id}.pdf")
    if resp is None:
        return "missing", None
    try:
        reader = PdfReader(io.BytesIO(resp.content))
        # extract_text() can return None for image-only pages — never let
        # one bad page discard the whole bulletin
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return "ok", parse_bulletin_text(text)
    except DesError:
        return "other", None  # different publication sharing the id space
    except Exception as e:  # noqa: BLE001 — malformed PDF: skip, don't abort
        log.warning("DES id %d: unreadable PDF (%s)", pdf_id, e)
        return "other", None


def _ingest_range(cfg: Config, session: requests.Session,
                  start_id: int, end_id: int) -> dict[str, int]:
    """Shared ingestion loop for fetch and backfill.

    Accumulates rows across all bulletins and upserts ONCE at the end —
    per-bulletin upserts rewrote each year partition hundreds of times
    over a long backfill (O(n^2) I/O).
    """
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    shaped_all: list[dict[str, Any]] = []
    bulletins = 0
    max_existing = 0

    for pdf_id in range(start_id, end_id + 1):
        status, payload = _read_bulletin(session, pdf_id)
        if status == "missing":
            continue  # no politeness sleep needed for an instant 404
        max_existing = pdf_id
        if status == "ok":
            date, entries = payload
            shaped = _to_ogd_shape(cfg, date, entries)
            shaped_all.extend(shaped)
            bulletins += 1
            log.info("DES id %d (%s): %d entries -> %d in-scope",
                     pdf_id, date, len(entries), len(shaped))
        time.sleep(SLEEP_S)

    rows, quarantined = normalize_records(cfg, shaped_all, source="des",
                                          fetched_at=fetched_at)
    changed = sum(upsert_rows(rows).values())
    write_quarantine(quarantined)

    if bulletins and not rows:
        log.warning("bulletins parsed but produced no in-scope rows — "
                    "check des_items/des_markets in sources.yaml for drift")
    return {"bulletins": bulletins, "rows": len(rows), "changed": changed,
            "quarantined": len(quarantined), "max_existing": max_existing}


def fetch(cfg: Config) -> int:
    """Cron entrypoint: ingest every bulletin newer than the saved state."""
    session = _session()
    state = _load_state()
    last_id = int(state.get("last_id", FIRST_KNOWN_ID - 1))

    listed = discover_bulletins(session)
    newest_listed = max((i for i, _ in listed), default=None)
    if newest_listed is None:
        end_id = last_id + PROBE_WINDOW
        log.warning("DES listing unavailable; probing ids %d..%d", last_id + 1, end_id)
    else:
        end_id = newest_listed
    if end_id <= last_id:
        print(f"des-fetch: no new bulletins (last_id={last_id})")
        return 0

    stats = _ingest_range(cfg, session, last_id + 1, end_id)

    # State advance rules (a 404 today may be published tomorrow):
    # - listing reachable: its newest id is authoritative — ids below it are
    #   already allocated and can never become bulletins later.
    # - listing down: only advance past ids that actually existed.
    new_last = newest_listed if newest_listed is not None \
        else max(last_id, stats["max_existing"])
    if new_last > last_id:
        state["last_id"] = new_last
        _save_state(state)

    print(f"des-fetch: ids {last_id + 1}..{end_id}, "
          f"{stats['bulletins']} bulletins, {stats['changed']} rows changed, "
          f"{stats['quarantined']} quarantined, state -> {new_last}")
    return 0


def backfill(cfg: Config, start_id: int | None = None,
             end_id: int | None = None) -> int:
    """One-time historical sweep over the upload id range."""
    session = _session()
    start_id = start_id or FIRST_KNOWN_ID
    listed_end = None
    if end_id is None:
        listed = discover_bulletins(session)
        listed_end = max((i for i, _ in listed), default=None)
        if listed_end is None:
            raise DesError("could not discover the latest bulletin id; pass --end-id")
        end_id = listed_end

    stats = _ingest_range(cfg, session, start_id, end_id)

    state = _load_state()
    # only a listing-derived end is authoritative; a manual --end-id may
    # point past what exists, so fall back to the highest id actually seen
    new_last = listed_end if listed_end is not None else stats["max_existing"]
    if new_last and new_last > int(state.get("last_id", 0)):
        state["last_id"] = new_last
        _save_state(state)

    print(f"des-backfill complete: ids {start_id}..{end_id}, "
          f"{stats['bulletins']} bulletins, {stats['changed']} rows changed, "
          f"{stats['quarantined']} quarantined")
    return 0
