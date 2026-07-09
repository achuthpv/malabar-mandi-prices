/* Malabar Mandi Prices — app logic.
   Hash routing (#/<slug>), fetches the static JSON API, renders everything.
   All API-derived strings go through textContent (never innerHTML). */
(function () {
  "use strict";

  const API = "api/v1";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const ALL_DISTRICTS = "__region__";

  const state = {
    commodities: [],
    slug: null,
    district: ALL_DISTRICTS,
    range: "3y",
    resolution: "weekly",
    dateFrom: null, // custom range overrides the preset when set
    dateTo: null,
    summary: null,
    latest: null,
    monthly: null,
    dailyCache: new Map(), // `${slug}:${year}` -> rows
  };

  const $ = (sel) => document.querySelector(sel);

  async function getJSON(path) {
    const resp = await fetch(path, { credentials: "omit" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${path}`);
    return resp.json();
  }

  function fmtPrice(v) {
    return v == null ? "–" : "₹" + Number(v).toLocaleString("en-IN");
  }

  function relDate(iso, asOf) {
    if (!iso) return "never";
    const days = Math.round((Date.parse(asOf || new Date().toISOString().slice(0, 10)) - Date.parse(iso)) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return `${days} days ago`;
    return `on ${iso}`;
  }

  /* ---------- boot ---------- */

  async function boot() {
    try {
      const doc = await getJSON(`${API}/commodities.json`);
      state.commodities = doc.commodities;
    } catch (err) {
      return fail(err);
    }
    renderTabs();
    window.addEventListener("hashchange", onRoute);
    $("#district-select").addEventListener("change", (e) => {
      state.district = e.target.value;
      renderAll();
    });
    $("#range-buttons").addEventListener("click", segHandler("range", "data-range", () => {
      clearCustomDates();
      renderChart();
    }));
    $("#res-buttons").addEventListener("click", segHandler("resolution", "data-res", renderChart));
    for (const id of ["#date-from", "#date-to"]) {
      $(id).addEventListener("change", onCustomDates);
    }
    $("#date-clear").addEventListener("click", () => {
      clearCustomDates();
      document.querySelectorAll("#range-buttons button")
        .forEach((b) => b.classList.toggle("active", b.dataset.range === state.range));
      renderChart();
    });
    initAsk();
    onRoute();
  }

  function onCustomDates() {
    state.dateFrom = $("#date-from").value || null;
    state.dateTo = $("#date-to").value || null;
    if (state.dateFrom && state.dateTo && state.dateFrom > state.dateTo) {
      [state.dateFrom, state.dateTo] = [state.dateTo, state.dateFrom];
    }
    const custom = !!(state.dateFrom || state.dateTo);
    $("#date-clear").hidden = !custom;
    document.querySelectorAll("#range-buttons button")
      .forEach((b) => b.classList.toggle("active", !custom && b.dataset.range === state.range));
    renderChart();
  }

  function clearCustomDates() {
    state.dateFrom = state.dateTo = null;
    $("#date-from").value = "";
    $("#date-to").value = "";
    $("#date-clear").hidden = true;
  }

  function segHandler(key, attr, after) {
    return (e) => {
      const btn = e.target.closest("button[" + attr + "]");
      if (!btn) return;
      state[key] = btn.getAttribute(attr);
      btn.parentElement.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      after();
    };
  }

  function slugFromHash() {
    const m = location.hash.match(/^#\/([a-z0-9-]+)/);
    return m && state.commodities.some((c) => c.slug === m[1]) ? m[1] : state.commodities[0].slug;
  }

  async function onRoute() {
    const slug = slugFromHash();
    if (slug === state.slug) return;
    state.slug = slug;
    document.querySelectorAll("#commodity-tabs button").forEach((b) =>
      b.classList.toggle("active", b.dataset.slug === slug));
    await loadCommodity();
  }

  async function loadCommodity() {
    $("#loading").hidden = false;
    $("#main").hidden = true;
    $("#error-box").hidden = true;
    try {
      const [summary, latest, monthly] = await Promise.all([
        getJSON(`${API}/analysis/${state.slug}/summary.json`),
        getJSON(`${API}/prices/${state.slug}/latest.json`),
        getJSON(`${API}/prices/${state.slug}/monthly.json`),
      ]);
      state.summary = summary;
      state.latest = latest;
      state.monthly = monthly;
    } catch (err) {
      return fail(err);
    }
    renderDistrictSelect();
    renderAll();
    $("#loading").hidden = true;
    $("#main").hidden = false;
    $("#footer-updated").textContent = "updated " + (state.summary.generated_at || "").slice(0, 10);
    await fetchSeasonality();
  }

  function fail(err) {
    console.warn(err && err.message ? err.message : err);
    $("#loading").hidden = true;
    $("#main").hidden = true;
    $("#error-box").hidden = false;
  }

  /* ---------- renders ---------- */

  function renderTabs() {
    const nav = $("#commodity-tabs");
    nav.textContent = "";
    for (const c of state.commodities) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = c.display;
      btn.dataset.slug = c.slug;
      btn.addEventListener("click", () => { location.hash = "#/" + c.slug; });
      nav.appendChild(btn);
    }
  }

  function renderDistrictSelect() {
    const sel = $("#district-select");
    const districts = Object.keys(state.summary.districts || {}).sort();
    sel.textContent = "";
    const optAll = document.createElement("option");
    optAll.value = ALL_DISTRICTS;
    optAll.textContent = "All districts (region)";
    sel.appendChild(optAll);
    for (const d of districts) {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      sel.appendChild(opt);
    }
    if (!districts.includes(state.district)) state.district = ALL_DISTRICTS;
    sel.value = state.district;
  }

  function currentView() {
    return state.district === ALL_DISTRICTS
      ? state.summary.region
      : (state.summary.districts || {})[state.district];
  }

  function renderAll() {
    renderFreshness();
    renderTiles();
    renderChart();
    renderSeasonality();
    renderMarkets();
  }

  function renderFreshness() {
    const badge = $("#freshness-badge");
    const view = currentView();
    if (!view) { badge.hidden = true; return; }
    const f = view.freshness;
    badge.textContent = "updated " + relDate(f.last_observed, state.summary.generated_at);
    badge.classList.toggle("stale", !!f.stale);
    badge.hidden = false;
  }

  function tile(label, value, hint, cls) {
    const el = document.createElement("div");
    el.className = "tile";
    const l = document.createElement("div"); l.className = "label"; l.textContent = label;
    const v = document.createElement("div"); v.className = "value" + (cls ? " " + cls : ""); v.textContent = value;
    el.append(l, v);
    if (hint) { const h = document.createElement("div"); h.className = "hint"; h.textContent = hint; el.appendChild(h); }
    return el;
  }

  function renderTiles() {
    const wrap = $("#stat-tiles");
    wrap.textContent = "";
    const view = currentView();
    if (!view) {
      wrap.appendChild(tile("No data", "–", "No observations for this area yet"));
      return;
    }
    const t = view.trend || {};
    const arrow = t.direction === "up" ? "▲" : t.direction === "down" ? "▼" : "▶";
    const cls = t.direction === "up" ? "up" : t.direction === "down" ? "down" : "";

    wrap.appendChild(tile("Latest price", fmtPrice(view.latest.modal_price),
      "per quintal, " + relDate(view.latest.date, state.summary.generated_at)));
    wrap.appendChild(tile("30-day median", fmtPrice(t.ma30),
      t.slope_pct_per_year != null ? `${arrow} ${Math.abs(t.slope_pct_per_year)}%/yr trend` : "trend needs more data", cls));
    wrap.appendChild(tile("Vs last year", t.yoy_pct == null ? "–" : `${t.yoy_pct > 0 ? "+" : ""}${t.yoy_pct}%`,
      "same month last year"));
    const s = view.signal;
    wrap.appendChild(tile("Vs typical " + (s ? s.month : ""),
      s == null ? "–" : `${s.vs_typical_pct > 0 ? "+" : ""}${s.vs_typical_pct}%`,
      s == null ? "needs seasonal history" : (s.vs_typical_pct >= 0 ? "above normal for this month" : "below normal for this month")));
  }

  /* ---------- chart ---------- */

  async function loadDaily(year) {
    const key = `${state.slug}:${year}`;
    if (!state.dailyCache.has(key)) {
      try {
        const doc = await getJSON(`${API}/prices/${state.slug}/daily/${year}.json`);
        const cols = doc.columns;
        const iDate = cols.indexOf("date"), iDistrict = cols.indexOf("district"),
              iModal = cols.indexOf("modal_price");
        state.dailyCache.set(key, doc.rows.map((r) => ({
          date: r[iDate], district: r[iDistrict], price: r[iModal],
        })));
      } catch (err) {
        state.dailyCache.set(key, []); // year file may not exist yet
      }
    }
    return state.dailyCache.get(key);
  }

  function median(nums) {
    if (!nums.length) return null;
    const s = [...nums].sort((a, b) => a - b);
    const mid = s.length >> 1;
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function isoWeekStart(dateStr) {
    const d = new Date(dateStr + "T00:00:00Z");
    const day = (d.getUTCDay() + 6) % 7; // Mon=0
    d.setUTCDate(d.getUTCDate() - day);
    return d.toISOString().slice(0, 10);
  }

  function rangeYears(from, to) {
    const years = [];
    for (let y = from; y <= to; y++) years.push(y);
    return years;
  }

  /* collapse raw rows to one point per day (median across markets),
     optionally then one per ISO week */
  function collapse(daily, weekly) {
    const byDay = new Map();
    for (const r of daily) {
      if (!byDay.has(r.date)) byDay.set(r.date, []);
      byDay.get(r.date).push(r.price);
    }
    let points = [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([date, prices]) => ({ date, price: median(prices) }));
    if (weekly) {
      const byWeek = new Map();
      for (const p of points) {
        const wk = isoWeekStart(p.date);
        if (!byWeek.has(wk)) byWeek.set(wk, []);
        byWeek.get(wk).push(p.price);
      }
      points = [...byWeek.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
        .map(([date, prices]) => ({ date, price: median(prices) }));
    }
    return points;
  }

  async function renderChart() {
    const el = $("#chart");
    const note = $("#chart-note");
    const asOf = state.summary.generated_at ? state.summary.generated_at.slice(0, 10) : new Date().toISOString().slice(0, 10);
    const endYear = +asOf.slice(0, 4);
    const custom = !!(state.dateFrom || state.dateTo);

    let rows;
    if (custom) {
      const years = state.monthly.years_available || [endYear];
      const from = state.dateFrom || `${years[0] || endYear}-01-01`;
      const to = state.dateTo || asOf;
      const perYear = await Promise.all(
        rangeYears(+from.slice(0, 4), +to.slice(0, 4)).map(loadDaily));
      let daily = perYear.flat().filter((r) => r.date >= from && r.date <= to);
      if (state.district !== ALL_DISTRICTS) daily = daily.filter((r) => r.district === state.district);
      const points = collapse(daily, state.resolution === "weekly");
      note.textContent = points.length
        ? `Showing ${from} to ${to} (${state.resolution} median).`
        : `No reported prices between ${from} and ${to} for this selection.`;
      window.MandiChart.render(el, points, {
        resolution: state.resolution, label: labelFor(),
      });
      return;
    }
    if (state.range === "all") {
      // monthly medians (compact) for the full history
      const series = state.district === ALL_DISTRICTS
        ? state.monthly.region
        : (state.monthly.districts || {})[state.district] || [];
      rows = series.map((m) => ({ date: m.month + "-15", price: m.median }));
      note.textContent = "Monthly median of reported market prices.";
      window.MandiChart.render(el, rows, { resolution: "monthly", label: labelFor() });
      return;
    }

    const yearsBack = state.range === "1y" ? 1 : 3;
    const perYear = await Promise.all(
      rangeYears(endYear - yearsBack, endYear).map(loadDaily));
    const cutoff = new Date(Date.parse(asOf) - yearsBack * 365 * 86400000).toISOString().slice(0, 10);

    let daily = perYear.flat().filter((r) => r.date >= cutoff);
    if (state.district !== ALL_DISTRICTS) daily = daily.filter((r) => r.district === state.district);

    const points = collapse(daily, state.resolution === "weekly");
    note.textContent = (state.resolution === "weekly" ? "Weekly" : "Daily") +
      " median of reported market prices. Gaps are days with no reporting.";

    if (!points.length) {
      el.textContent = "";
      const msg = document.createElement("p");
      msg.className = "chart-note";
      msg.textContent = "No price data in this range yet. The daily pipeline is accumulating history.";
      el.appendChild(msg);
      note.textContent = "";
      return;
    }
    window.MandiChart.render(el, points, {
      resolution: state.resolution === "weekly" ? "weekly" : "daily",
      label: labelFor(),
    });
  }

  function labelFor() {
    const c = state.commodities.find((c) => c.slug === state.slug);
    return (c ? c.display : state.slug) + " (₹/quintal)";
  }

  /* ---------- seasonality ---------- */

  function renderSeasonality() {
    const wrap = $("#season-bars");
    const narrative = $("#narrative");
    wrap.textContent = "";
    narrative.textContent = "";

    const view = currentView();
    const season = viewSeasonality();

    const sellMonths = new Set(((view || {}).best_sell || {}).months || []);
    const buyMonths = new Set(((view || {}).best_buy || {}).months || []);

    const index = season ? season.index : new Array(12).fill(null);
    const usable = index.filter((v) => v != null);
    const max = usable.length ? Math.max(...usable) : 1;
    const min = usable.length ? Math.min(...usable) : 1;

    for (let m = 0; m < 12; m++) {
      const col = document.createElement("div");
      col.className = "bar-col" + (index[m] == null ? " na" : "");
      const bar = document.createElement("div");
      bar.className = "bar";
      if (sellMonths.has(m + 1)) bar.classList.add("sell");
      else if (buyMonths.has(m + 1)) bar.classList.add("buy");
      const val = index[m];
      const pct = val == null ? 8 : 12 + 82 * ((val - min) / Math.max(max - min, 0.001));
      bar.style.height = pct + "%";
      bar.title = val == null ? MONTHS[m] + ": not enough data"
        : `${MONTHS[m]}: ${val >= 1 ? "+" : ""}${((val - 1) * 100).toFixed(1)}% vs yearly average`;
      const label = document.createElement("div");
      label.className = "m";
      label.textContent = MONTHS[m];
      col.append(bar, label);
      wrap.appendChild(col);
    }

    for (const line of (view || {}).narrative || []) {
      const p = document.createElement("p");
      p.textContent = line;
      narrative.appendChild(p);
    }
    const sell = (view || {}).best_sell;
    if (sell) {
      const p = document.createElement("p");
      p.className = "confidence";
      p.textContent = `Confidence: ${sell.confidence} (${sell.n_years} years of history). Historical tendency only — not financial advice.`;
      narrative.appendChild(p);
    }
  }

  // seasonality index lives in the seasonality endpoint's shape inside summary?
  // summary carries best_sell/best_buy but not the 12-month index; fetch lazily.
  let seasonCache = new Map();
  function viewSeasonality() {
    const key = state.slug + ":" + state.district;
    return seasonCache.get(key) || null;
  }

  async function fetchSeasonality() {
    try {
      const doc = await getJSON(`${API}/analysis/${state.slug}/seasonality.json`);
      seasonCache.set(state.slug + ":" + ALL_DISTRICTS, doc.seasonality);
      for (const [d, s] of Object.entries(doc.districts || {})) {
        seasonCache.set(state.slug + ":" + d, s);
      }
    } catch (err) { /* seasonality optional */ }
    renderSeasonality();
  }

  /* ---------- markets table ---------- */

  function benchmarkDistricts() {
    const out = new Set();
    for (const [name, view] of Object.entries(state.summary.districts || {})) {
      if (view && view.benchmark) out.add(name);
    }
    return out;
  }

  function renderMarkets() {
    const tbody = $("#markets-table tbody");
    tbody.textContent = "";
    const asOf = state.summary.generated_at ? state.summary.generated_at.slice(0, 10) : null;
    const benchmarks = benchmarkDistricts();
    let markets = state.latest.markets || [];
    if (state.district !== ALL_DISTRICTS) {
      markets = markets.filter((m) => m.district === state.district);
    }

    // median of recently-reported markets (≤7 days behind the newest shown)
    const newest = markets.reduce((a, m) => (m.date > a ? m.date : a), "");
    const recentCutoff = newest
      ? new Date(Date.parse(newest) - 7 * 86400000).toISOString().slice(0, 10) : "";
    const recentPrices = markets.filter((m) => m.date >= recentCutoff)
      .map((m) => m.modal_price);
    const med = median(recentPrices);

    renderSpreadLine();

    for (const m of markets) {
      const tr = document.createElement("tr");
      const days = asOf ? Math.round((Date.parse(asOf) - Date.parse(m.date)) / 86400000) : 0;
      if (days > 30) tr.className = "stale-row";
      const isRecent = m.date >= recentCutoff;
      const vs = med && isRecent ? (m.modal_price / med - 1) * 100 : null;

      const tdMarket = document.createElement("td");
      tdMarket.textContent = m.market + " ";
      if (benchmarks.has(m.district)) {
        const star = document.createElement("span");
        star.className = "bench-star";
        star.textContent = "★";
        star.title = "benchmark market (outside the home region)";
        tdMarket.appendChild(star);
      }
      tr.appendChild(tdMarket);

      const rest = [
        [m.district, ""],
        [fmtPrice(m.modal_price), "num"],
        [vs == null ? "–" : `${vs >= 0 ? "+" : ""}${vs.toFixed(1)}%`,
         "num vsmed " + (vs == null ? "" : vs >= 2 ? "up" : vs <= -2 ? "down" : "")],
        [`${fmtPrice(m.min_price)}–${fmtPrice(m.max_price)}`, "num"],
        [relDate(m.date, asOf), ""],
      ];
      for (const [text, cls] of rest) {
        const td = document.createElement("td");
        if (cls.trim()) td.className = cls.trim();
        td.textContent = text;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    if (!markets.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.textContent = "No markets reporting for this selection yet.";
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
  }

  function renderSpreadLine() {
    const el = $("#spread-line");
    const s = state.latest.spread;
    // region-wide figure; hide when a single district is selected
    if (!s || state.district !== ALL_DISTRICTS || s.spread_pct < 2) {
      el.hidden = true;
      return;
    }
    el.textContent = `Widest current gap: ${s.high.market} (${s.high.district}) ` +
      `${fmtPrice(s.high.modal_price)} vs ${s.low.market} (${s.low.district}) ` +
      `${fmtPrice(s.low.modal_price)} — ${s.spread_pct}% apart across ` +
      `${s.n_markets} recently-reporting markets.`;
    el.hidden = false;
  }

  /* ---------- ask-a-question box ---------- */

  function initAsk() {
    const sug = $("#ask-suggestions");
    for (const ex of window.MandiAssistant.examples.slice(0, 4)) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip chip-suggest";
      chip.textContent = ex;
      chip.addEventListener("click", () => ask(ex));
      sug.appendChild(chip);
    }
    $("#ask-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("#ask-input").value.trim();
      if (q) ask(q);
    });

    // demo AI mode controls
    const syncLLM = () => {
      const on = window.MandiAssistant.llmEnabled();
      $("#llm-off-controls").hidden = on;
      $("#llm-on-controls").hidden = !on;
      if (on) {
        $("#llm-badge").textContent =
          "AI answers: ON — " + window.MandiAssistant.llmLabel() + " (this tab)";
      }
      $("#ask-mode-note").textContent = on
        ? "AI answers are ON for this tab (your key, sent only to the endpoint you configured). Not financial advice."
        : "Answers come from this site's own historical analysis — no data leaves your browser. Not financial advice.";
    };
    $("#llm-provider").addEventListener("change", () => {
      const openai = $("#llm-provider").value === "openai";
      document.querySelectorAll(".llm-openai-only")
        .forEach((el) => { el.hidden = !openai; });
      $("#llm-key").placeholder = openai ? "sk-… (leave empty for local Ollama)" : "sk-ant-…";
    });
    $("#llm-enable").addEventListener("click", () => {
      const errEl = $("#llm-error");
      errEl.hidden = true;
      try {
        window.MandiAssistant.enableLLM({
          provider: $("#llm-provider").value,
          key: $("#llm-key").value,
          baseUrl: $("#llm-url").value,
          model: $("#llm-model").value,
        });
        $("#llm-key").value = "";
        syncLLM();
      } catch (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
      }
    });
    $("#llm-disable").addEventListener("click", () => {
      window.MandiAssistant.disableLLM();
      syncLLM();
    });
    syncLLM();
  }

  async function ask(question) {
    const log = $("#ask-log");
    const btn = $("#ask-btn");
    $("#ask-input").value = "";

    const qEl = document.createElement("p");
    qEl.className = "ask-q";
    qEl.textContent = question;
    const aEl = document.createElement("p");
    aEl.className = "ask-a";
    aEl.textContent = "…";
    log.prepend(aEl);
    log.prepend(qEl);

    btn.disabled = true;
    try {
      aEl.textContent = await window.MandiAssistant.answer(question);
    } catch (err) {
      aEl.textContent = "Sorry — could not answer that right now.";
    } finally {
      btn.disabled = false;
    }
  }

  boot();
})();
