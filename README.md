# Malabar Mandi Prices

A lightweight, mobile-friendly dashboard tracking wholesale mandi prices of
**arecanut, black pepper and coconut** in **Calicut (Kozhikode) & North Kerala**
— with seasonality analysis that estimates the best months of the year to
sell (and buy) each commodity.

- **Data**: [Agmarknet](https://agmarknet.gov.in/) daily mandi prices via the
  official [data.gov.in](https://data.gov.in/) API; ~5-year history backfilled
  from [CEDA, Ashoka University](https://agmarknet.ceda.ashoka.edu.in/).
- **Updates**: twice daily via GitHub Actions (no servers).
- **Storage**: plain CSV files in this repo — diffable, reviewable, free.
- **Site + API**: static site on GitHub Pages; the JSON files under
  `/api/v1/` are a public read API described by [`site/openapi.json`](site/openapi.json),
  ready to be wired to an LLM for "why did prices change?" Q&A.
- **Stack**: Python (pipeline, runs only in CI) + vanilla HTML/CSS/JS with
  [uPlot](https://github.com/leeoniya/uPlot) (vendored, ~13 KB gz). No build step.
- **Interactive**: hover/touch price probe, preset ranges (1Y/3Y/All),
  weekly/daily resolution, custom From/To dates, drag-to-zoom
  (double-click resets), district filter.
- **"Ask about the market"**: a question box that answers
  sell/buy-timing, "is now a good time?", "why are prices low?", "which
  market pays most?", price and trend questions from the site's own
  analysis — rule-based, free, runs entirely in the browser.
- **Demo AI mode (opt-in)**: under the ask box, "AI mode (demo)" lets you
  paste your own Anthropic API key to get Claude-written answers *for that
  browser tab only* — ideal for demos. The key lives in sessionStorage
  (gone when the tab closes), is sent only to api.anthropic.com, and
  there's no shared key or backend, so nobody else can spend or spam it.
  Turn it off with one click; the rule engine is always the fallback.
  See SECURITY.md for the full threat model.

## How it works

```
data.gov.in OGD API ──► mandi fetch ──► data/prices/{slug}/{year}.csv   (committed)
CEDA archive (once) ──► mandi backfill ──┘
                            │
              mandi analyze + publish   (CI only)
                            ▼
        site/api/v1/*.json  +  site/index.html  ──►  GitHub Pages
```

Analysis is interpretable arithmetic, no ML: detrended seasonal indices
(each year normalized by its own median, then medianed across years),
30/90-day rolling medians, YoY change, and a "current vs typical for this
month" signal. Every number carries its support (`n_obs`, `n_years`) and a
confidence label.

## Setup (one-time)

1. **Create a GitHub repo** and push this project to `main`.
2. **Get a free data.gov.in API key**: sign up at <https://data.gov.in>,
   My Account → Generate API Key.
3. **Add the key as a secret**: repo → Settings → Secrets and variables →
   Actions → New repository secret, name `DATA_GOV_IN_API_KEY`.
4. **Enable GitHub Pages**: repo → Settings → Pages → Source: **GitHub Actions**.
5. **Enable workflows**: the `daily-data` workflow starts collecting on its
   next scheduled run (or trigger it manually via Actions → daily-data →
   Run workflow).
6. Edit `servers[0].url` in `site/openapi.json` to your Pages URL
   (e.g. `https://<user>.github.io/<repo>`).

### Historical backfill (once, recommended)

The default backfill pulls from the **Agmarknet 2.0 public report API**
(the same endpoint the "Daily Price and Arrival Report" page on
agmarknet.gov.in uses) — no token needed, price data back to 2021:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt && pip install --no-deps -e pipeline
python -m mandi backfill --dry-run   # verify commodity/state ID matching
python -m mandi backfill --years 5
git add data/ && git commit -m "data: agmarknet historical backfill" && git push
```

Alternative: `python -m mandi backfill --source ceda` uses the
[CEDA](https://agmarknet.ceda.ashoka.edu.in/) archive instead (register for
a free token, set `CEDA_API_TOKEN`; non-commercial use with attribution).

Seasonality analysis needs at least 2 full years of history; 5 years gives
stable estimates with honest confidence labels.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt -r pipeline/requirements-dev.txt
pip install --no-deps -e pipeline

export DATA_GOV_IN_API_KEY="..."   # your personal key
python -m mandi discover           # see what the feed reports today
python -m mandi fetch              # upsert today's prices into data/
python -m mandi analyze            # compute seasonality/trends -> build/
python -m mandi publish            # generate site/api/v1/*.json

# serve the site locally
python -m http.server -d site 8000

# tests
python -m pytest pipeline/tests -q          # unit tests
python e2e/validate_contract.py             # OpenAPI + contract check
python -m playwright install chromium       # once
python -m pytest e2e -q                     # Playwright E2E (frozen dataset)
```

## Adding a commodity, district or market

Edit [`config/sources.yaml`](config/sources.yaml) — one block, no code
changes. Run `python -m mandi discover` first to learn the exact commodity /
district spellings the feed uses (they are surprising: "Keralam",
"Kozhikode(Calicut)", "Arecanut(Betelnut/Supari)"). For a new commodity,
also add the slug to the `Slug` enum in `site/openapi.json`. Everything
else (fetch, analysis, API, frontend tabs) picks the change up
automatically. Re-run `python -m mandi backfill` after adding areas to
fetch their history.

Districts support two extra keys, built for **arbitrage watching**:

```yaml
- name: Uttara Kannada          # Karnataka's Sirsi arecanut belt
  ogd_names: ["Uttara Kannada", "Karwar(Uttar Kannand)"]
  benchmark: true               # reference area: shown in tables, spreads and
                                # the district selector, but EXCLUDED from the
                                # home region's pooled seasonality analysis
  markets: ["Sirsi", "Yellapur"]  # optional whitelist (substring match);
                                  # omit to take every market in the district
```

The shipped config tracks three benchmark areas: the Sirsi belt +
Shivamogga (arecanut reference), Ernakulam/Kochi terminals (pepper
reference) and Coimbatore/Pollachi (coconut reference). The dashboard
shows a **spread line** ("widest current gap: X vs Y — Z% apart"), a
**Vs median** column per market, and ★ marks on benchmark markets; the
API exposes the same via `spread` in `latest.json` / `summary.json`.
Spreads are computed only across markets that reported within the last
7 days — a stale price is not an arbitrage opportunity — and are quoted
before transport, quality and market-fee differences.

## API

Interactive spec: [`site/openapi.json`](site/openapi.json). Start with:

| Endpoint | What |
|---|---|
| `/api/v1/index.json` | all endpoints |
| `/api/v1/analysis/{slug}/summary.json` | one-call summary: latest price, trend, YoY, seasonal signal, best sell/buy windows, plain-language narrative — designed for LLM tool-use |
| `/api/v1/prices/{slug}/monthly.json` | compact monthly-median history |
| `/api/v1/prices/{slug}/daily/{year}.json` | full daily rows |

GitHub Pages serves everything with `Access-Control-Allow-Origin: *`, so
the API is consumable from any origin, including LLM tools.

## Scaling / migration path

CSV-in-git handles this workload for a decade (~700 KB/year). If the project
outgrows it (many states/commodities, write API): the CSV schema maps 1:1 to
a table — import into Cloudflare D1 / Supabase / Turso free tiers and point
`publish.py` at the DB instead of the CSVs. Nothing in the frontend changes.

## Security practices

See [SECURITY.md](SECURITY.md): hashed+pinned dependencies, `pip-audit` in CI,
actions pinned to commit SHAs, least-privilege workflow permissions, no CDN
(uPlot vendored), strict CSP, `textContent`-only DOM writes, API key only in
GitHub Secrets, anomalous data quarantined rather than dropped.

## Attribution & disclaimer

Price data © [Agmarknet](https://agmarknet.gov.in/) (Directorate of Marketing
& Inspection, Government of India), served through the
[Open Government Data Platform India](https://data.gov.in/). Historical
archive courtesy of [CEDA, Ashoka University](https://ceda.ashoka.edu.in/) —
used non-commercially with attribution.

Seasonal estimates are historical tendencies, **not financial advice**.
