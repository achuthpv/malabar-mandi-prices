"""OGD (data.gov.in) API client with retries, pagination and rate limiting.

The commodity/district filters on the OGD API do fuzzy token matching
(e.g. filters[commodity]="Pepper ungarbled" also returns "Black pepper"
rows), so we fetch per-state and filter client-side with exact names.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Any, Iterator

import requests

from .config import Config

log = logging.getLogger(__name__)

PAGE_LIMIT = 1000  # max records per request with a personal API key
MAX_RETRIES = 3
TIMEOUT_S = 30
COURTESY_SLEEP_S = 1.0
USER_AGENT = "malabar-mandi-dashboard (github; personal non-commercial project)"


class FetchError(RuntimeError):
    """Raised when the OGD API cannot be reached after retries."""


def api_key() -> str:
    key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
    if not key:
        raise FetchError(
            "DATA_GOV_IN_API_KEY is not set. Get a free key at "
            "https://data.gov.in (My Account > Generate API Key)."
        )
    return key


def _redact(text: str) -> str:
    """Strip the api-key query param from error text before it reaches logs.

    The OGD API takes the key as a URL parameter and requests embeds full
    URLs in exception messages. GitHub Actions masks registered secrets,
    but local terminal runs would print the key verbatim without this.
    """
    return re.sub(r"api-key=[^&\s]+", "api-key=REDACTED", text)


def _request_page(
    cfg: Config, session: requests.Session, params: dict[str, Any]
) -> dict[str, Any]:
    url = f"{cfg.base_url}/{cfg.ogd_resource}"
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT_S)
            if resp.status_code == 429:
                wait = 30 * attempt
                log.warning("HTTP 429 rate-limited; sleeping %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_error = _redact(str(e))
            if attempt < MAX_RETRIES:
                # jitter is for retry spreading, not crypto
                backoff = (2**attempt) + random.uniform(0, 1)  # noqa: S311
                log.warning("fetch attempt %d failed (%s); retrying in %.1fs",
                            attempt, last_error, backoff)
                time.sleep(backoff)
    raise FetchError(f"OGD API request failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_state_records(
    cfg: Config, state_name: str, session: requests.Session | None = None
) -> Iterator[dict[str, Any]]:
    """Yield every raw record the OGD resource currently holds for a state."""
    session = session or _make_session()
    offset = 0
    while True:
        params = {
            "api-key": api_key(),
            "format": "json",
            "limit": PAGE_LIMIT,
            "offset": offset,
            "filters[state.keyword]": state_name,
        }
        page = _request_page(cfg, session, params)
        records = page.get("records") or []
        yield from records
        count = len(records)
        offset += count
        total = int(page.get("total") or 0)
        if count == 0 or offset >= total:
            return
        time.sleep(COURTESY_SLEEP_S)


def fetch_all(cfg: Config) -> list[dict[str, Any]]:
    """Fetch raw records for every configured state name (spelling variants)."""
    session = _make_session()
    out: list[dict[str, Any]] = []
    for state in cfg.state_names:
        rows = list(fetch_state_records(cfg, state, session))
        log.info("state %r: %d raw records", state, len(rows))
        out.extend(rows)
        time.sleep(COURTESY_SLEEP_S)
    return out


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s
