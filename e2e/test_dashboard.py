"""Playwright E2E: the 'smooth UX' gate. Run against a frozen data build."""

from __future__ import annotations

import json
import urllib.request  # noqa: F401  (used by endpoint validity test)

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
        expect(rows.nth(i).locator("td").nth(2)).to_have_text("Wayanad")


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


def test_custom_date_range_filters_chart(page: Page, site_url: str, errors):
    _open(page, site_url, "/#/coconut")
    page.fill("#date-from", "2024-01-01")
    page.fill("#date-to", "2024-06-30")
    page.dispatch_event("#date-to", "change")
    page.wait_for_timeout(400)
    expect(page.locator("#chart canvas").first).to_be_visible()
    note = page.locator("#chart-note").inner_text()
    assert "2024-01-01" in note and "2024-06-30" in note
    # preset buttons deactivate while a custom range is active
    assert page.locator("#range-buttons button.active").count() == 0
    page.click("#date-clear")
    page.wait_for_timeout(300)
    assert page.locator("#range-buttons button.active").count() == 1
    assert errors == []


def test_ask_box_sell_timing(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "When should I sell black pepper?")
    page.click("#ask-btn")
    page.wait_for_timeout(600)
    answer = page.locator(".ask-a").first.inner_text()
    assert "Black Pepper" in answer
    assert "confidence" in answer
    assert any(m in answer for m in
               ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])


def test_ask_box_now_question(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "Is now a good time to sell coconut?")
    page.click("#ask-btn")
    page.wait_for_timeout(600)
    answer = page.locator(".ask-a").first.inner_text()
    assert "₹" in answer and "%" in answer
    assert "Coconut" in answer


def test_why_answer_cites_news_when_available(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "Why are black pepper prices high?")
    page.click("#ask-btn")
    page.wait_for_timeout(700)
    answer = page.locator(".ask-a").first.inner_text()
    assert "Vietnam crop shortfall" in answer  # fixture headline cited
    assert "Test Wire" in answer


def test_why_answer_without_news_says_so(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "Why are coconut prices low?")  # no news seeded
    page.click("#ask-btn")
    page.wait_for_timeout(700)
    answer = page.locator(".ask-a").first.inner_text()
    assert "No recent news collected" in answer


def test_ask_box_help_for_unknown(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "hello there")
    page.click("#ask-btn")
    page.wait_for_timeout(600)
    answer = page.locator(".ask-a").first.inner_text()
    assert "Try" in answer or "ask" in answer.lower()


def test_ask_suggestion_chips(page: Page, site_url: str):
    _open(page, site_url)
    chips = page.locator(".chip-suggest")
    assert chips.count() >= 3
    chips.first.click()
    page.wait_for_timeout(600)
    assert len(page.locator(".ask-a").first.inner_text()) > 30


def test_spread_line_and_benchmark_star(page: Page, site_url: str):
    _open(page, site_url, "/#/arecanut")
    spread = page.locator("#spread-line")
    expect(spread).to_be_visible()
    text = spread.inner_text()
    assert "Sirsi APMC" in text and "%" in text and "gap" in text.lower()
    # benchmark market rows are starred; home markets are not
    star_row = page.locator("#markets-table tbody tr", has_text="Sirsi APMC").first
    expect(star_row.locator(".bench-star")).to_have_count(1)
    home_row = page.locator("#markets-table tbody tr", has_text="Kasargod Market").first
    expect(home_row.locator(".bench-star")).to_have_count(0)
    # Vs median is per-variety: the premium Sirsi rows themselves must show
    # a positive signed percentage (not just some cell somewhere)
    sirsi_rows = page.locator("#markets-table tbody tr", has_text="Sirsi APMC")
    for i in range(sirsi_rows.count()):
        vs = sirsi_rows.nth(i).locator("td.vsmed").inner_text()
        assert vs.startswith("+") and vs.endswith("%"), f"Sirsi row {i}: {vs}"


def test_variety_selector_and_panel(page: Page, site_url: str):
    _open(page, site_url, "/#/arecanut")
    # selector visible with both types; panel lists Rashi (premium) first
    expect(page.locator("#variety-select")).to_be_visible()
    options = page.locator("#variety-select option").all_inner_texts()
    assert "All types" in options and "Rashi" in options and "Chali" in options
    rows = page.locator("#varieties-table tbody tr").all_inner_texts()
    assert len(rows) == 2 and "Rashi" in rows[0]

    # selecting a type filters the markets table and annotates the chart
    page.select_option("#variety-select", "Rashi")
    page.wait_for_timeout(400)
    types = page.locator("#markets-table tbody tr td:nth-child(2)").all_inner_texts()
    assert types and all(t == "Rashi" for t in types)
    assert "type: Rashi" in page.locator("#chart-note").inner_text()
    painted = page.evaluate(
        """() => { const c = document.querySelector('#chart canvas');
        const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
        for (let i=3;i<d.length;i+=4) if (d[i]!==0) return true; return false; }""")
    assert painted


def test_variety_selector_hidden_for_single_type(page: Page, site_url: str):
    _open(page, site_url, "/#/coconut")
    expect(page.locator("#variety-select")).to_be_hidden()
    expect(page.locator("#varieties-panel")).to_be_hidden()


def test_benchmark_excluded_from_seasonality_but_selectable(page: Page, site_url: str):
    _open(page, site_url, "/#/arecanut")
    options = page.locator("#district-select option").all_inner_texts()
    assert any("Uttara Kannada" in o for o in options)
    page.select_option("#district-select", "Uttara Kannada")
    page.wait_for_timeout(300)
    rows = page.locator("#markets-table tbody tr")
    assert rows.count() == 2  # one per variety (Chali, Rashi)
    for i in range(2):
        expect(rows.nth(i)).to_contain_text("Sirsi APMC")


def test_ask_which_market(page: Page, site_url: str):
    _open(page, site_url)
    page.fill("#ask-input", "Which market pays most for arecanut?")
    page.click("#ask-btn")
    page.wait_for_timeout(600)
    answer = page.locator(".ask-a").first.inner_text()
    assert "Sirsi APMC" in answer
    assert "gap" in answer.lower() and "%" in answer


def test_ai_mode_off_by_default(page: Page, site_url: str):
    _open(page, site_url)
    # collapsed details, rules-mode note, no config stored
    expect(page.locator("#llm-off-controls")).to_be_hidden()  # inside closed <details>
    note = page.locator("#ask-mode-note").inner_text()
    assert "no data leaves your browser" in note
    assert page.evaluate("sessionStorage.getItem('mandi_demo_llm_config')") is None


def test_ai_mode_rejects_bad_key(page: Page, site_url: str):
    _open(page, site_url)
    page.click("#llm-details summary")
    page.fill("#llm-key", "not-a-real-key")
    page.click("#llm-enable")
    assert page.evaluate("sessionStorage.getItem('mandi_demo_llm_config')") is None
    err = page.locator("#llm-error")
    expect(err).to_be_visible()
    assert "sk-ant" in err.inner_text()


def test_ai_mode_rejects_insecure_url(page: Page, site_url: str):
    _open(page, site_url)
    page.click("#llm-details summary")
    page.select_option("#llm-provider", "openai")
    page.fill("#llm-url", "http://evil.example.com/v1")  # plain http, not localhost
    page.fill("#llm-model", "some-model")
    page.click("#llm-enable")
    assert page.evaluate("sessionStorage.getItem('mandi_demo_llm_config')") is None
    expect(page.locator("#llm-error")).to_be_visible()


def test_ai_mode_openai_compatible(page: Page, site_url: str):
    """OpenAI-compatible endpoint: enable -> ask (stubbed) -> disable."""
    captured = []

    def stub(route):
        req = route.request
        captured.append({
            "auth": req.headers.get("authorization"),
            "body": json.loads(req.post_data),
        })
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "choices": [{"message": {"role": "assistant",
                                     "content": "Stubbed GPT answer."}}]}))

    page.route("https://api.openai.com/**", stub)
    _open(page, site_url)
    page.click("#llm-details summary")
    page.select_option("#llm-provider", "openai")
    page.fill("#llm-url", "https://api.openai.com/v1")
    page.fill("#llm-model", "gpt-4o-mini")
    page.fill("#llm-key", "sk-test-openai-key")
    page.click("#llm-enable")
    expect(page.locator("#llm-on-controls")).to_be_visible()
    assert "gpt-4o-mini @ api.openai.com" in page.locator("#llm-badge").inner_text()

    page.fill("#ask-input", "When should I sell coconut?")
    page.click("#ask-btn")
    page.wait_for_timeout(700)
    assert page.locator(".ask-a").first.inner_text() == "Stubbed GPT answer."
    assert len(captured) == 1
    assert captured[0]["auth"] == "Bearer sk-test-openai-key"
    assert captured[0]["body"]["model"] == "gpt-4o-mini"
    assert captured[0]["body"]["messages"][0]["role"] == "system"

    page.click("#llm-disable")
    assert page.evaluate("sessionStorage.getItem('mandi_demo_llm_config')") is None


def test_ai_mode_enable_ask_disable(page: Page, site_url: str):
    """Full demo lifecycle with a stubbed Anthropic API."""
    calls = []

    def stub(route):
        calls.append(route.request.headers.get("x-api-key"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "content": [{"type": "text",
                         "text": "Stubbed Claude answer about pepper."}]}))

    page.route("https://api.anthropic.com/**", stub)
    _open(page, site_url)
    page.click("#llm-details summary")
    page.fill("#llm-key", "sk-ant-test0123456789abcdefghij")
    page.click("#llm-enable")
    expect(page.locator("#llm-on-controls")).to_be_visible()
    assert "AI answers are ON" in page.locator("#ask-mode-note").inner_text()
    # key never rendered anywhere in the page
    assert "sk-ant-test" not in page.content()

    page.fill("#ask-input", "When should I sell black pepper?")
    page.click("#ask-btn")
    page.wait_for_timeout(700)
    assert page.locator(".ask-a").first.inner_text() == "Stubbed Claude answer about pepper."
    assert calls == ["sk-ant-test0123456789abcdefghij"]

    # turn off: key forgotten, rules answer again (no further API calls)
    page.click("#llm-disable")
    assert page.evaluate("sessionStorage.getItem('mandi_demo_llm_config')") is None
    page.fill("#ask-input", "When should I sell black pepper?")
    page.click("#ask-btn")
    page.wait_for_timeout(700)
    answer = page.locator(".ask-a").first.inner_text()
    assert "confidence" in answer and len(calls) == 1


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
