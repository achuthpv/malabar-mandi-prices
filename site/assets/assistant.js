/* Market Q&A assistant.
   Rule-based intent matching over the published analysis JSON — free,
   offline-capable, nothing leaves the browser. LLM-ready: call
   window.MandiAssistant.setLLM(async (question, context) => "answer")
   to route questions to a model instead; the rules stay as fallback. */
(function () {
  "use strict";

  const API = "api/v1";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let cache = null;
  let llm = null;

  async function getJSON(path) {
    const resp = await fetch(path, { credentials: "omit" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  async function load() {
    if (cache) return cache;
    const commodities = (await getJSON(`${API}/commodities.json`)).commodities;
    const docs = await Promise.all(
      commodities.map((c) => getJSON(`${API}/analysis/${c.slug}/summary.json`)));
    cache = {
      commodities: commodities,
      bySlug: Object.fromEntries(docs.map((d) => [d.commodity, d])),
    };
    return cache;
  }

  const SYNONYMS = {
    "arecanut": ["arecanut", "areca", "betel", "betelnut", "supari", "adakka", "adike"],
    "black-pepper": ["pepper", "kurumulaku", "kali mirch", "mirch"],
    "coconut": ["coconut", "thengu", "thenga", "nariyal", "copra"],
  };

  function fmtPrice(v) {
    return v == null ? "–" : "₹" + Number(v).toLocaleString("en-IN");
  }

  function detectCommodities(q, data) {
    const hits = [];
    for (const c of data.commodities) {
      const names = SYNONYMS[c.slug] || [c.slug, c.display.toLowerCase()];
      if (names.some((n) => q.includes(n))) hits.push(c.slug);
    }
    return hits.length ? hits : data.commodities.map((c) => c.slug);
  }

  function detectView(q, doc) {
    for (const [name, view] of Object.entries(doc.districts || {})) {
      if (view && q.includes(name.toLowerCase())) return view;
    }
    return doc.region;
  }

  function detectIntent(q) {
    // "where" first: market comparison beats timing words
    if (/\b(where|which market|which mandi|arbitrage|spread|best market|highest price|lowest price)\b/.test(q)) return "where";
    // "now" next: "is now a good time to sell" must not match sell-timing
    if (/\b(now|today|right now|current(ly)?|at the moment|this (week|month))\b/.test(q)) return "now";
    if (/\b(sell|selling)\b/.test(q) && /\b(when|best|month|time|season)\b/.test(q)) return "sell";
    if (/\b(buy|buying|purchase|cheap)\b/.test(q)) return "buy";
    if (/\b(why|reason|dropped|fall|fell|low|high|rose|jump|change|crash)\b/.test(q)) return "why";
    if (/\b(price|rate|cost|how much|latest)\b/.test(q)) return "price";
    if (/\b(trend|forecast|predict|next|future|going|outlook)\b/.test(q)) return "trend";
    if (/\b(sell)\b/.test(q)) return "sell";
    return "help";
  }

  function windowText(w) {
    if (!w) return null;
    const span = w.month_names[0] + "–" + w.month_names[w.month_names.length - 1];
    return { span: span, premium: Math.abs(w.premium_pct), confidence: w.confidence, years: w.n_years };
  }

  function staleCaveat(view) {
    if (view && view.freshness && view.freshness.stale) {
      return `Careful: this area last reported on ${view.freshness.last_observed} — the numbers may be out of date.`;
    }
    return null;
  }

  function answerFor(slug, intent, data, q) {
    const doc = data.bySlug[slug];
    if (!doc) return null;
    const view = detectView(q, doc) || doc.region;
    const c = data.commodities.find((x) => x.slug === slug);
    const name = c ? c.display : slug;
    if (!view) return `${name}: no data collected yet for this area.`;

    const lines = [];
    const sell = windowText(view.best_sell);
    const buy = windowText(view.best_buy);
    const s = view.signal;
    const t = view.trend || {};
    const area = view.level;

    if (intent === "sell") {
      if (sell) {
        lines.push(`${name}: historically the strongest months in ${area} are ${sell.span} ` +
          `(about ${sell.premium}% above the yearly average; ${sell.confidence} confidence, ` +
          `${sell.years} years of data).`);
        if (s && s.vs_typical_pct >= 3) {
          lines.push(`Right now prices are already ${s.vs_typical_pct}% above what's typical ` +
            `for ${s.month} — selling into current strength is also reasonable.`);
        }
      } else {
        lines.push(`${name}: not enough multi-year history yet to name the best selling months. ` +
          `The daily pipeline is still accumulating data.`);
      }
    } else if (intent === "buy") {
      if (buy) {
        lines.push(`${name}: prices in ${area} tend to be lowest around ${buy.span} ` +
          `(about ${buy.premium}% below the yearly average) — historically the better buying window.`);
      } else {
        lines.push(`${name}: not enough history yet to estimate the cheapest months.`);
      }
    } else if (intent === "now") {
      lines.push(`${name}: latest price ${fmtPrice(view.latest.modal_price)}/quintal ` +
        `(reported ${view.latest.date}).`);
      if (s) {
        const dir = s.vs_typical_pct >= 0 ? "above" : "below";
        lines.push(`That is ${Math.abs(s.vs_typical_pct)}% ${dir} what's typical for ${s.month}.`);
        if (sell) {
          if (s.vs_typical_pct >= 3) {
            lines.push(`Prices are running hot for the season — a decent moment to sell even ` +
              `outside the usual ${sell.span} peak.`);
          } else if (s.vs_typical_pct <= -3) {
            lines.push(`Prices are soft for the season. If you can hold, the historically ` +
              `stronger window is ${sell.span}.`);
          } else {
            lines.push(`Prices are close to seasonal norms. The historically stronger window ` +
              `is ${sell.span}.`);
          }
        }
      } else if (sell) {
        lines.push(`The historically stronger window is ${sell.span}.`);
      }
    } else if (intent === "why") {
      const bits = [];
      if (s) {
        const dir = s.vs_typical_pct >= 0 ? "above" : "below";
        bits.push(`current prices are ${Math.abs(s.vs_typical_pct)}% ${dir} the seasonal norm for ${s.month}`);
      }
      if (t.yoy_pct != null) {
        bits.push(`${t.yoy_pct >= 0 ? "up" : "down"} ${Math.abs(t.yoy_pct)}% vs the same period last year`);
      }
      if (t.direction && t.direction !== "flat") {
        bits.push(`the 12-month trend is ${t.direction} (${t.slope_pct_per_year}%/yr)`);
      }
      lines.push(`${name} in ${area}: ` + (bits.length ? bits.join("; ") + "." : "no clear signal in the data."));
      if (buy && s && s.vs_typical_pct < 0 && view.best_buy &&
          view.best_buy.months.includes(new Date().getMonth() + 1)) {
        lines.push(`Note this is normal for the calendar: ${buy.span} is historically the ` +
          `weakest stretch of the year, usually driven by harvest arrivals and demand cycles.`);
      }
      lines.push(`This dashboard doesn't read market news yet, so it can only explain what ` +
        `the price history shows — not one-off events.`);
    } else if (intent === "where") {
      const sp = doc.spread;
      if (sp) {
        lines.push(`${name}: highest recent price ${fmtPrice(sp.high.modal_price)} at ` +
          `${sp.high.market} (${sp.high.district}); lowest ${fmtPrice(sp.low.modal_price)} at ` +
          `${sp.low.market} (${sp.low.district}) — a ${sp.spread_pct}% gap across ` +
          `${sp.n_markets} markets reporting in the last ${sp.window_days} days.`);
        lines.push(`Mind that gaps vs distant benchmark markets (★) come before transport, ` +
          `quality/variety and market-fee differences.`);
      } else {
        lines.push(`${name}: not enough recently-reporting markets to compare prices.`);
      }
    } else if (intent === "price") {
      lines.push(`${name}: latest ${fmtPrice(view.latest.modal_price)}/quintal in ${area} ` +
        `(reported ${view.latest.date}); 30-day median ${fmtPrice(t.ma30)}.`);
    } else if (intent === "trend") {
      const dirWord = t.direction === "up" ? "rising" : t.direction === "down" ? "falling" : "flat";
      lines.push(`${name} in ${area}: the 12-month trend is ${dirWord}` +
        (t.slope_pct_per_year != null ? ` (${t.slope_pct_per_year}%/yr)` : "") +
        (t.yoy_pct != null ? `, ${t.yoy_pct >= 0 ? "+" : ""}${t.yoy_pct}% vs last year` : "") + ".");
      if (sell) lines.push(`Seasonally, the strongest months are ${sell.span}.`);
      lines.push(`No forecasting yet — these are historical tendencies, not predictions.`);
    }

    const caveat = staleCaveat(view);
    if (caveat) lines.push(caveat);
    return lines.join(" ");
  }

  const EXAMPLES = [
    "When should I sell black pepper?",
    "Which market pays most for arecanut?",
    "Is now a good time to sell coconut?",
    "Why are arecanut prices low?",
    "When is coconut cheapest to buy?",
  ];

  async function answer(question) {
    const q = String(question || "").toLowerCase().trim();
    if (!q) return "Ask me something like: " + EXAMPLES.slice(0, 3).join(" · ");

    const data = await load();

    if (llm) {
      try {
        const context = { question: question, summaries: data.bySlug };
        const out = await llm(question, context);
        if (out) return out;
      } catch (e) { /* fall back to rules */ }
    }

    const intent = detectIntent(q);
    if (intent === "help") {
      return "I can answer questions about prices, timing and trends for the tracked " +
        "commodities. Try: " + EXAMPLES.join(" · ");
    }
    const slugs = detectCommodities(q, data);
    const answers = slugs.map((s) => answerFor(s, intent, data, q)).filter(Boolean);
    return answers.join("\n\n");
  }

  window.MandiAssistant = {
    answer: answer,
    examples: EXAMPLES,
    setLLM: function (fn) { llm = fn; },
  };
})();
