"""E2E fixtures: build the site from a frozen synthetic dataset and serve it.

The synthetic data has a known seasonal shape (December peak) across
several districts and markets, so charts, seasonality bars and tiles all
render meaningfully — without depending on live data.
"""

from __future__ import annotations

import math
import shutil
import socket
import sys
import threading
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "src"))

from mandi.analyze import analyze_all  # noqa: E402
from mandi.config import load_config  # noqa: E402
from mandi.publish import publish_all  # noqa: E402
from mandi.store import upsert_rows  # noqa: E402

TODAY = date(2026, 7, 8)
FETCHED_AT = "2026-07-08T18:00:00+00:00"

MARKETS = {
    # Sirsi APMC sits in a benchmark district (Uttara Kannada) and trades
    # ~15% above the home region — exercises the spread/arbitrage view
    "arecanut": [("Kasargod", "Kasargod Market"), ("Kannur", "Kuthuparambu Market"),
                 ("Uttara Kannada", "Sirsi APMC")],
    "black-pepper": [("Wayanad", "Pulpally Market"), ("Kannur", "Kannur Market"),
                     ("Kasargod", "Kasargod Market")],
    "coconut": [("Kozhikode", "Mukkom Market"), ("Kannur", "Kannur Market")],
}
BASE_PRICE = {"arecanut": 40000, "black-pepper": 65000, "coconut": 5000}
PEAK_MONTH = {"arecanut": 1, "black-pepper": 3, "coconut": 12}


def _price(slug: str, day: date, market_offset: int) -> int:
    phase = (day.month - PEAK_MONTH[slug]) / 12 * 2 * math.pi
    seasonal = 1 + 0.09 * math.cos(phase)
    trend = 1 + 0.04 * (day.year - 2021)
    return int(BASE_PRICE[slug] * seasonal * trend) + market_offset * 150


def _seed_rows() -> list[dict]:
    rows = []
    day = date(2021, 7, 1)
    while day <= TODAY:
        if day.weekday() < 5:  # weekends = natural gaps
            for slug, markets in MARKETS.items():
                for i, (district, market) in enumerate(markets):
                    price = _price(slug, day, i)
                    if market == "Sirsi APMC":
                        price = int(price * 1.15)
                    rows.append({
                        "date": day.isoformat(), "district": district,
                        "market": market, "commodity_slug": slug,
                        "variety": "Other", "grade": "FAQ",
                        "min_price": price - 300, "max_price": price + 300,
                        "modal_price": price, "unit": "Rs/quintal",
                        "source": "ogd", "fetched_at": FETCHED_AT,
                    })
        day += timedelta(days=1)
    return rows


@pytest.fixture(scope="session")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("site-build")
    site = root / "site"
    site.mkdir()
    for name in ("index.html", "openapi.json"):
        shutil.copy(REPO_ROOT / "site" / name, site / name)
    shutil.copytree(REPO_ROOT / "site" / "assets", site / "assets")

    data = root / "data"
    upsert_rows(_seed_rows(), base=data)

    cfg = load_config()
    analysis = analyze_all(cfg, base=data, today=TODAY)
    publish_all(cfg, generated_at=FETCHED_AT, api_dir=site / "api" / "v1",
                data_base=data, analysis=analysis)
    return site


@pytest.fixture(scope="session")
def site_url(built_site: Path):
    handler = partial(SimpleHTTPRequestHandler, directory=str(built_site))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
