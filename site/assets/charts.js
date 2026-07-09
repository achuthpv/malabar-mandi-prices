/* Price chart built on uPlot. Exposes window.MandiChart.
   Series values are medians across the selected area's markets.
   Missing market days stay as real gaps (nulls, spanGaps=false). */
(function () {
  "use strict";

  let plot = null;
  let resizeObserver = null;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function fmtPrice(v) {
    if (v == null) return "–";
    return "₹" + Number(v).toLocaleString("en-IN");
  }

  /* rows: [{date: "YYYY-MM-DD", price: number}] sorted by date, one per day.
     Returns uPlot data with nulls inserted for unobserved days so gaps show. */
  function toSeries(rows, fillDaily) {
    if (!rows.length) return [[], []];
    const xs = [];
    const ys = [];
    if (!fillDaily) {
      for (const r of rows) {
        xs.push(Date.parse(r.date) / 1000);
        ys.push(r.price);
      }
      return [xs, ys];
    }
    const byDate = new Map(rows.map((r) => [r.date, r.price]));
    const start = new Date(rows[0].date + "T00:00:00Z");
    const end = new Date(rows[rows.length - 1].date + "T00:00:00Z");
    for (let t = start.getTime(); t <= end.getTime(); t += 86400000) {
      const iso = new Date(t).toISOString().slice(0, 10);
      xs.push(t / 1000);
      ys.push(byDate.has(iso) ? byDate.get(iso) : null);
    }
    return [xs, ys];
  }

  function render(el, rows, opts) {
    const daily = opts.resolution === "daily";
    const data = toSeries(rows, daily);

    if (plot) {
      plot.destroy();
      plot = null;
    }
    el.textContent = "";

    const width = el.clientWidth || 320;
    const height = Math.min(Math.max(220, Math.round(window.innerHeight * 0.34)), 360);

    plot = new uPlot(
      {
        width: width,
        height: height,
        padding: [10, 8, 0, 0],
        // drag on the x axis to zoom into a period; double-click resets
        cursor: { drag: { x: true, y: false, setScale: true } },
        legend: { show: true },
        scales: { x: { time: true } },
        axes: [
          {
            stroke: cssVar("--muted"),
            grid: { stroke: cssVar("--line"), width: 1 },
            ticks: { stroke: cssVar("--line") },
          },
          {
            stroke: cssVar("--muted"),
            grid: { stroke: cssVar("--line"), width: 1 },
            ticks: { stroke: cssVar("--line") },
            size: 56,
            values: (u, ticks) => ticks.map((v) => (v >= 1000 ? Math.round(v / 100) / 10 + "k" : v)),
          },
        ],
        series: [
          {},
          {
            label: opts.label || "Modal price",
            stroke: cssVar("--accent"),
            width: 2,
            spanGaps: false,
            points: { show: rows.length < 60 },
            value: (u, v) => fmtPrice(v),
          },
        ],
      },
      data,
      el
    );

    if (resizeObserver) resizeObserver.disconnect();
    resizeObserver = new ResizeObserver(() => {
      if (plot && el.clientWidth) plot.setSize({ width: el.clientWidth, height: height });
    });
    resizeObserver.observe(el);
  }

  window.MandiChart = { render: render, fmtPrice: fmtPrice };
})();
