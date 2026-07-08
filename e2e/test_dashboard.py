"""Playwright E2E: the 'smooth UX' gate. Run against a frozen data build."""

from __future__ import annotations

import json
import urllib.request

import pytest
from playwright.sync_api import Page, expect

SLUGS = ["arecanut", "black-pepper", "coconut"]


@pytest.fixture()
def errors(page: Page):
    """Collect console errors and failed requests during a test."""
    sink: list[str] = []
    page.on("console", lambda msg: sink.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: sink.append(str(err)))
    page.on("requestfailed", lambda req: sink.append(f"request failed: {req.url}"))
    return sink


def _open(page: Page, site_url: str, path: str = "/") -> None:
    page.goto(site_url + path)
    page.wait_for_selector("#main:not([hidden])", timeout=10000)


def test_home_loads_without_errors(page: Page, site_url: str, errors):
    _open(page, site_url)
    expect(page.locator("h1")).to_contain_text("Malabar Mandi Prices")
    assert errors == [], f"console/network errors: {errors}"


@pytest.mark.parametrize("slug", SLUGS)
def test_each_commodity_renders_chart(page: Page, site_url: str, slug: str):
    _open(page, site_url, f"/#/{slug}")
    canvas = page.locator("#chart canvas").first
    expect(canvas).to_be_visible()
    # canvas must not be blank: sample pixels for any non-transparent paint
    painted = page.evaluate(
        """() => {
        const c = document.querySelector('#chart canvas');
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return true;
        return false;
        }"""
    )
    assert painted, f"{slug}: chart canvas is blank"


def test_tab_selection_survives_reload(page: Page, site_url: str):
    _open(page, site_url, "/#/black-pepper")
    expect(page.locator("#commodity-tabs button.active")).to_have_text("Black Pepper")
    page.reload()
    page.wait_for_selector("#main:not([hidden])")
    expect(page.locator("#commodity-tabs button.active")).to_have_text("Black Pepper")


def test_district_selector_filters_markets(page: Page, site_url: str):
    _open(page, site_url, "/#/black-pepper")
    rows_all = page.locator("#markets-table tbody tr").count()
    page.select_option("#district-select", "Wayanad")
    page.wait_for_timeout(200)
    rows = page.locator("#markets-table tbody tr")
    assert 0 < rows.count() < rows_all
    for i in range(rows.count()):
        expect(rows.nth(i).locator("td").nth(1)).to_have_text("Wayanad")


def test_seasonality_panel(page: Page, site_url: str):
    _open(page, site_url, "/#/coconut")
    expect(page.locator("#season-bars .bar-col")).to_have_count(12)
    # best sell/buy highlighted
    assert page.locator("#season-bars .bar.sell").count() >= 2
    assert page.locator("#season-bars .bar.buy").count() >= 2
    narrative = page.locator("#narrative").inner_text()
    assert "sells about" in narrative
    assert "confidence" in narrative.lower()


def test_tiles_show_numbers_not_nan(page: Page, site_url: str):
    for slug in SLUGS:
        _open(page, site_url, f"/#/{slug}")
        tiles = page.locator("#stat-tiles").inner_text()
        assert "NaN" not in tiles and "undefined" not in tiles, f"{slug}: {tiles}"
        assert "₹" in tiles


def test_range_and_resolution_toggles(page: Page, site_url: str, errors):
    _open(page, site_url)
    for rng in ("1y", "all", "3y"):
        page.click(f'#range-buttons button[data-range="{rng}"]')
        page.wait_for_timeout(250)
        expect(page.locator("#chart canvas").first).to_be_visible()
    page.click('#res-buttons button[data-res="daily"]')
    page.wait_for_timeout(250)
    expect(page.locator("#chart canvas").first).to_be_visible()
    assert errors == []


@pytest.mark.parametrize("viewport", [(360, 740), (375, 667)])
def test_mobile_no_horizontal_overflow(page: Page, site_url: str, viewport):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    _open(page, site_url)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"horizontal overflow of {overflow}px at {viewport}"
    # key touch targets are >= 40px tall (44 with padding in real browsers)
    for sel in ("#commodity-tabs button", "#district-select"):
        box = page.locator(sel).first.bounding_box()
        assert box and box["height"] >= 40, f"{sel} too small: {box}"


def test_api_failure_shows_friendly_error(page: Page, site_url: str):
    page.route("**/api/v1/**", lambda route: route.abort())
    page.goto(site_url)
    page.wait_for_selector("#error-box:not([hidden])", timeout=10000)
    expect(page.locator("#error-box")).to_contain_text("Could not load data")
    expect(page.locator("#main")).to_be_hidden()


def test_dark_mode_renders(page: Page, site_url: str):
    page.emulate_media(color_scheme="dark")
    _open(page, site_url)
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    r, g, b = [int(x) for x in bg.strip("rgb()").split(",")[:3]]
    assert r + g + b < 200, f"dark mode background not dark: {bg}"
    expect(page.locator("#stat-tiles .tile").first).to_be_visible()


def test_openapi_and_all_endpoints_valid_json(site_url: str):
    with urllib.request.urlopen(site_url + "/openapi.json") as resp:  # noqa: S310
        spec = json.load(resp)
    assert spec["openapi"].startswith("3.1")
    with urllib.request.urlopen(site_url + "/api/v1/index.json") as resp:  # noqa: S310
        index = json.load(resp)
    assert index["endpoints"]
    for endpoint in index["endpoints"]:
        with urllib.request.urlopen(site_url + endpoint) as resp:  # noqa: S310
            doc = json.load(resp)
        assert doc["generated_at"], f"{endpoint} missing generated_at"
        assert doc["attribution"], f"{endpoint} missing attribution"
