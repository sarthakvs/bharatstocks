"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = { symbol: null, horizon: "short", range: "6mo", data: null };
const dash = { horizon: "short", period: "1w", universe: "nifty50", sort: "score" };
let chart = null, chartObs = null, suggestItems = [], suggestActive = -1, searchTimer = null, searchAbort = null;
window.stageTimer = null;
window.autoRefresh = null;
window.autoRefreshTop = null;
window.candleSeries = null;
window.volSeries = null;
window.overlaySeries = {};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
const fmt = (v, d = 2) =>
  (v === null || v === undefined || Number.isNaN(v)) ? "—" :
    Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

const inr = (v) => (v === null || v === undefined) ? "—" : "₹" + fmt(v, 2);

function pct(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  const c = v >= 0 ? "up" : "down";
  const s = v >= 0 ? "+" : "";
  return `<span class="${c}">${s}${fmt(v, 2)}%</span>`;
}

function crore(v) {
  if (!v) return "—";
  if (v >= 1e12) return "₹" + fmt(v / 1e12, 2) + " L Cr";
  if (v >= 1e7) return "₹" + fmt(v / 1e7, 0) + " Cr";
  return inr(v);
}

const actionColor = (action) =>
  action === "BUY" ? "var(--green)" : action === "SELL" ? "var(--red)" : "var(--amber)";

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let msg = "Request failed";
    try { msg = (await r.json()).error || msg; } catch (e) { }
    throw new Error(msg);
  }
  return r.json();
}

// ---------------------------------------------------------------------------
// Search + autocomplete
// ---------------------------------------------------------------------------
const searchEl = $("search"), suggestEl = $("suggest");

searchEl.addEventListener("input", () => {
  const q = searchEl.value.trim();
  clearTimeout(searchTimer);
  if (q.length < 1) { hideSuggest(); return; }
  searchTimer = setTimeout(() => runSearch(q), 180);
});

searchEl.addEventListener("keydown", (e) => {
  if (suggestEl.classList.contains("hidden")) {
    if (e.key === "Enter" && searchEl.value.trim()) selectSymbol(searchEl.value.trim().toUpperCase());
    return;
  }
  if (e.key === "ArrowDown") { e.preventDefault(); moveSuggest(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveSuggest(-1); }
  else if (e.key === "Enter") {
    e.preventDefault();
    if (suggestActive >= 0 && suggestItems[suggestActive]) selectSymbol(suggestItems[suggestActive].symbol);
    else if (searchEl.value.trim()) selectSymbol(searchEl.value.trim().toUpperCase());
  } else if (e.key === "Escape") hideSuggest();
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".searchwrap")) hideSuggest();
});

async function runSearch(q) {
  if (searchAbort) searchAbort.abort();
  searchAbort = new AbortController();
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: searchAbort.signal });
    const data = await r.json();
    suggestItems = data.results || [];
    renderSuggest();
  } catch (e) { /* aborted / network */ }
}

function renderSuggest() {
  if (!suggestItems.length) { hideSuggest(); return; }
  suggestActive = -1;
  suggestEl.innerHTML = suggestItems.map((it, i) => `
    <div class="suggest-item" data-i="${i}">
      <span class="si-sym">${it.symbol}</span>
      <span class="si-name">${it.name || ""}</span>
      <span class="si-tag">${it.exchange || (it.sector || "NSE")}</span>
    </div>`).join("");
  suggestEl.classList.remove("hidden");
  suggestEl.querySelectorAll(".suggest-item").forEach((el) => {
    el.addEventListener("click", () => selectSymbol(suggestItems[+el.dataset.i].symbol));
  });
}

function moveSuggest(dir) {
  const els = suggestEl.querySelectorAll(".suggest-item");
  if (!els.length) return;
  suggestActive = (suggestActive + dir + els.length) % els.length;
  els.forEach((el, i) => el.classList.toggle("active", i === suggestActive));
}

function hideSuggest() { suggestEl.classList.add("hidden"); suggestActive = -1; }

function selectSymbol(sym) {
  hideSuggest();
  searchEl.value = sym;
  go(STOCK_HASH + encodeURIComponent(sym));
}

// ---------------------------------------------------------------------------
// Navigation / routing (separate "pages" via the URL hash so the browser
// Back button works too; returning to the dashboard always re-scans so prices
// are never stale)
// ---------------------------------------------------------------------------
const STOCK_HASH = "#/stock/";
const DASH_HASH = "#/";

function go(hash) {
  if (location.hash === hash) route();   // same hash -> force a re-route
  else location.hash = hash;             // otherwise hashchange fires route()
}

function route() {
  const h = location.hash || "";
  if (h.startsWith(STOCK_HASH)) {
    const sym = decodeURIComponent(h.slice(STOCK_HASH.length)).toUpperCase();
    if (sym) openStock(sym);
    else openDashboard();
  } else {
    openDashboard();
  }
}

function openStock(sym) {
  // stop dashboard refresh, show only the stock page
  if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
  $("dashboard").classList.add("hidden");
  searchEl.value = sym;
  window.scrollTo({ top: 0 });
  loadStock(sym);
}

function openDashboard() {
  // stop stock refresh, show only the dashboard, and re-scan for fresh prices
  state.symbol = null;
  if (window.autoRefresh) clearInterval(window.autoRefresh);
  if (window.stageTimer) clearInterval(window.stageTimer);
  $("detail").classList.add("hidden");
  $("detail-loader").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  searchEl.value = "";
  window.scrollTo({ top: 0 });
  loadTop();   // refresh -> no stale prices on going back
}

window.addEventListener("hashchange", route);

// ---------------------------------------------------------------------------
// Stock detail
// ---------------------------------------------------------------------------
async function loadStock(symbol, silent = false) {
  state.symbol = symbol;
  
  if (!silent) {
      if (window.stageTimer) clearInterval(window.stageTimer);
      const stages = [
        `Fetching live market data for ${symbol}...`,
        `Computing technical indicators...`,
        `Running quantitative analysis...`,
        `Finalizing predictions...`
      ];
      let stageIdx = 0;
      $("detail-loader").classList.remove("hidden");
      $("detail-loader").innerHTML = `<div class="spinner"></div><div id="loader-text" style="margin-top:12px;color:var(--muted)">${stages[0]}</div>`;
      window.stageTimer = setInterval(() => {
        stageIdx = (stageIdx + 1) % stages.length;
        const el = $("loader-text");
        if (el) el.textContent = stages[stageIdx];
      }, 1000);
  }
  
  let data;
  try {
    data = await api(`/api/stock/${encodeURIComponent(symbol)}?horizon=${state.horizon}&range=${state.range}`);
  } catch (e) {
    if (window.stageTimer) clearInterval(window.stageTimer);
    if (!silent) {
      $("detail-loader").classList.remove("hidden");
      $("detail-loader").innerHTML =
        `<div>⚠ ${e.message}</div>` +
        `<button class="back-btn" style="margin-top:14px" onclick="go('${DASH_HASH}')">← Back to dashboard</button>`;
    }
    return;
  }
  
  if (window.stageTimer) clearInterval(window.stageTimer);
  state.data = data;
  
  if (!silent) {
      $("detail-loader").classList.add("hidden");
      $("detail").classList.remove("hidden");
  }
  
  renderDetail();
  
  if (!silent) {
      if (window.autoRefresh) clearInterval(window.autoRefresh);
      window.autoRefresh = setInterval(() => {
          if (state.symbol === symbol) {
              loadStock(symbol, true);
          } else {
              clearInterval(window.autoRefresh);
          }
      }, 15000);
  }
}

function renderDetail() {
  const d = state.data;
  $("d-name").textContent = d.name;
  $("d-symbol").textContent = d.symbol;
  $("d-sector").textContent = d.sector || "—";
  const elPrice = $("d-price");
  const currentText = elPrice.textContent;
  const newPriceText = inr(d.quote.price);
  
  if (currentText !== newPriceText && currentText !== "—") {
      const oldVal = parseFloat(currentText.replace(/[^0-9.-]+/g,""));
      const newVal = d.quote.price;
      const isUp = newVal >= oldVal;
      elPrice.classList.remove("flash-up", "flash-down");
      void elPrice.offsetWidth; // force DOM reflow to restart animation
      elPrice.classList.add(isUp ? "flash-up" : "flash-down");
  }
  elPrice.textContent = newPriceText;

  const ch = d.quote.change, chp = d.quote.change_pct;
  const cl = (ch ?? 0) >= 0 ? "up" : "down", sg = (ch ?? 0) >= 0 ? "+" : "";
  
  const elChange = $("d-change");
  elChange.className = "change " + cl;
  elChange.textContent = (ch === null) ? "" : `${sg}${fmt(ch)} (${sg}${fmt(chp)}%)`;

  renderRecommendation();
  renderChart();
  renderStats();
  $("disclaimer").textContent = d.disclaimer;

  // horizon toggle reflects current
  $("horizon-toggle").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", b.dataset.h === state.horizon));
  $("range-toggle").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", b.dataset.r === state.range));
}

function renderRecommendation() {
  const a = state.data.analysis[state.horizon];
  const i = state.data.indicators;
  
  // Update the top-right timestamp
  if (state.data.as_of) {
      $("asof").textContent = "As of " + state.data.as_of;
  }

  const color = actionColor(a.action);
  const score = a.score ?? 0;

  $("rec-score").textContent = a.score ?? "—";
  $("rec-label").textContent = a.label;
  $("rec-label").style.color = color;
  $("rec-conf").textContent = (a.confidence ?? "—") + "%";
  
  const rw = $("rec-winrate");
  if (rw) {
      if (a.backtest && a.backtest.win_rate !== null) {
        rw.innerHTML = `${a.backtest.win_rate}% <small class="muted" style="font-weight:normal">(${a.backtest.wins}/${a.backtest.trades} winning trades)</small>`;
      } else {
        rw.textContent = "—";
      }
  }
  
  $("rec-summary").textContent = a.summary || "";

  const fg = $("gauge-fg");
  const C = 327;
  fg.style.strokeDashoffset = C * (1 - score / 100);
  fg.style.stroke = color;

  // Levels
  const lv = a.levels;
  const cells = [];
  if (lv) {
    cells.push({ k: "Entry", v: inr(lv.entry) });
    cells.push({ k: a.action === "HOLD" ? "Protective stop" : "Stop-loss", v: inr(lv.stop_loss), c: "var(--red)" });
    if (lv.target1) cells.push({ k: "Target 1", v: inr(lv.target1), c: "var(--green)" });
    if (lv.target2) cells.push({ k: "Target 2", v: inr(lv.target2), c: "var(--green)" });
    if (!lv.target1) cells.push({ k: "Key level", v: inr(lv.key_level) });
    cells.push({ k: "Risk / share", v: inr(lv.risk_per_share) });
    cells.push({ k: "Risk %", v: fmt(lv.risk_pct) + "%" });
    if (lv.risk_reward) cells.push({ k: "Risk : Reward", v: "1 : " + lv.risk_reward });
  }
  $("levels").innerHTML = cells.map((c) =>
    `<div class="level"><div class="lk">${c.k}</div><div class="lv" style="color:${c.c || "var(--text)"}">${c.v}</div></div>`
  ).join("");

  $("rec-pos").innerHTML = (a.positives || []).map((p) => `<li>${p}</li>`).join("") || '<li class="muted">—</li>';
  $("rec-neg").innerHTML = (a.negatives || []).map((p) => `<li>${p}</li>`).join("") || '<li class="muted">—</li>';
}

// ---------------------------------------------------------------------------
// Chart (TradingView Lightweight Charts v4)
// ---------------------------------------------------------------------------
function renderChart() {
  const el = $("chart");
  const c = state.data.chart;
  if (!c.candles || !c.candles.length) {
    if (chartObs) { chartObs.disconnect(); chartObs = null; }
    if (chart) { chart.remove(); chart = null; }
    el.innerHTML = '<div class="loader">No chart data for this range.</div>';
    $("chart-legend").innerHTML = "";
    return;
  }
  
  // Reuse the existing chart only for a silent refresh of the SAME stock+range
  // (smooth live update). On a different stock/range, rebuild so overlays and
  // the time axis reset correctly.
  const chartKey = state.symbol + ":" + (c.range || state.range) + ":" + (c.intraday ? "i" : "d");
  const sameView = chart && window.candleSeries && window.volSeries && window.chartKey === chartKey;
  if (sameView) {
      window.candleSeries.setData(c.candles.map((d) => ({
        time: d.time, open: d.open, high: d.high, low: d.low, close: d.close,
      })));
      window.volSeries.setData(c.candles.map((d) => ({
        time: d.time, value: d.volume,
        color: d.close >= d.open ? "rgba(46,204,113,.4)" : "rgba(255,87,101,.4)",
      })));
      Object.entries(c.overlays || {}).forEach(([key, series]) => {
          if (window.overlaySeries && window.overlaySeries[key]) {
              window.overlaySeries[key].setData(series);
          }
      });
      return;
  }
  
  if (chartObs) { chartObs.disconnect(); chartObs = null; }
  if (chart) { chart.remove(); chart = null; }
  el.innerHTML = "";

  const width = el.clientWidth || el.parentElement.clientWidth || 600;
  chart = LightweightCharts.createChart(el, {
    width: width,
    height: 360,
    layout: { background: { color: "transparent" }, textColor: "#8a96ad", fontSize: 11 },
    grid: { vertLines: { color: "#1a2233" }, horzLines: { color: "#1a2233" } },
    rightPriceScale: { borderColor: "#263045" },
    timeScale: { borderColor: "#263045", timeVisible: c.intraday, secondsVisible: false },
    crosshair: { mode: 1 },
  });

  window.candleSeries = chart.addCandlestickSeries({
    upColor: "#2ecc71", downColor: "#ff5765",
    borderUpColor: "#2ecc71", borderDownColor: "#ff5765",
    wickUpColor: "#2ecc71", wickDownColor: "#ff5765",
  });
  window.candleSeries.setData(c.candles.map((d) => ({
    time: d.time, open: d.open, high: d.high, low: d.low, close: d.close,
  })));

  window.volSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" }, priceScaleId: "", color: "#33415c",
  });
  window.volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  window.volSeries.setData(c.candles.map((d) => ({
    time: d.time, value: d.volume,
    color: d.close >= d.open ? "rgba(46,204,113,.4)" : "rgba(255,87,101,.4)",
  })));

  // MA overlays
  const maColors = { sma20: "#4c8dff", sma50: "#ffb648", sma200: "#b06bff" };
  const legend = [];
  window.overlaySeries = {};
  Object.entries(c.overlays || {}).forEach(([key, series]) => {
    const ls = chart.addLineSeries({ color: maColors[key] || "#888", lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
    ls.setData(series);
    window.overlaySeries[key] = ls;
    legend.push(`<span><span class="dot" style="background:${maColors[key]}"></span><b>${key.toUpperCase()}</b></span>`);
  });
  legend.push('<span><span class="dot" style="background:#2ecc71"></span>Up candle</span>');
  legend.push('<span><span class="dot" style="background:#ff5765"></span>Down candle</span>');
  $("chart-legend").innerHTML = legend.join("");

  chart.timeScale().fitContent();

  // Keep the chart sized to its container (handles hidden->visible + window resize).
  chartObs = new ResizeObserver((entries) => {
    const w = entries[0].contentRect.width;
    if (chart && w > 0) chart.applyOptions({ width: Math.floor(w) });
  });
  chartObs.observe(el);

  window.chartKey = chartKey;   // mark which stock+range this chart holds
}

// ---------------------------------------------------------------------------
// Stats grid
// ---------------------------------------------------------------------------
function renderStats() {
  const i = state.data.indicators, f = state.data.fundamentals, q = state.data.quote;
  const rsiTag = i.rsi14 == null ? "" :
    i.rsi14 >= 70 ? ' <small class="down">OB</small>' : i.rsi14 <= 30 ? ' <small class="up">OS</small>' : "";

  const stats = [
    ["News Sentiment", state.data.sentiment?.articles ? `${state.data.sentiment.score > 0 ? "+" : ""}${state.data.sentiment.score} <small>(${state.data.sentiment.articles} articles)</small>` : "—"],
    ["RSI (14)", i.rsi14 == null ? "—" : fmt(i.rsi14, 0) + rsiTag],
    ["MACD hist", i.macd_hist == null ? "—" : `<span class="${i.macd_hist >= 0 ? "up" : "down"}">${fmt(i.macd_hist, 2)}</span>`],
    ["ADX (trend)", i.adx == null ? "—" : fmt(i.adx, 0)],
    ["Stochastic %K", i.stoch_k == null ? "—" : fmt(i.stoch_k, 0)],
    ["ATR (14)", i.atr14 == null ? "—" : `${inr(i.atr14)} <small>(${fmt(i.atr_pct, 1)}%)</small>`],
    ["20-DMA", inr(i.sma20)],
    ["50-DMA", inr(i.sma50)],
    ["200-DMA", inr(i.sma200)],
    ["Return 1W", pct(i.ret_1w)],
    ["Return 1M", pct(i.ret_1m)],
    ["Return 3M", pct(i.ret_3m)],
    ["Return 6M", pct(i.ret_6m)],
    ["Return 1Y", pct(i.ret_1y)],
    ["Volume", i.vol == null ? "—" : Number(i.vol).toLocaleString("en-IN")],
    ["P/E", f.pe == null ? "—" : fmt(f.pe, 1)],
    ["P/B", f.pb == null ? "—" : fmt(f.pb, 1)],
    ["ROE", f.roe == null ? "—" : fmt(f.roe * 100, 1) + "%"],
    ["Div yield", f.dividend_yield == null ? "—" : fmt(f.dividend_yield * (f.dividend_yield < 1 ? 100 : 1), 2) + "%"],
    ["Market cap", crore(q.market_cap)],
    ["52W High", inr(q.fifty_two_high)],
    ["52W Low", inr(q.fifty_two_low)],
    ["Analyst target", f.target_mean == null ? "—" : inr(f.target_mean)],
  ];
  $("stats-grid").innerHTML = stats.map(([k, v]) =>
    `<div class="stat"><div class="sk">${k}</div><div class="sv">${v}</div></div>`).join("");
}

// ---------------------------------------------------------------------------
// Dashboard (top picks)
// ---------------------------------------------------------------------------
async function loadTop(silent = false) {
  if (!silent) {
      if (window.stageTimer) clearInterval(window.stageTimer);
      $("dash-loader").classList.remove("hidden");
      $("dash-loader").innerHTML = `<div class="spinner" style="display:inline-block;vertical-align:middle;margin-right:8px;width:16px;height:16px;border-width:2px"></div><span id="dash-loader-text">Scanning market... (first scan fetches live data)</span>`;
      
      let scanStage = 0;
      const scanStages = ["Scanning market...", "Fetching live quotes...", "Computing daily indicators...", "Calculating quantitative scores..."];
      window.stageTimer = setInterval(() => {
        scanStage = (scanStage + 1) % scanStages.length;
        const el = $("dash-loader-text");
        if (el) el.textContent = scanStages[scanStage];
      }, 1000);
  }
  
  try {
    const d = await api(`/api/top?horizon=${dash.horizon}&period=${dash.period}&universe=${dash.universe}&sort=${dash.sort}&limit=12`);
    renderTable("tbl-buy", d.buys, d.period_return_key, d.period);
    renderTable("tbl-sell", d.sells, d.period_return_key, d.period);
    
    if (!silent) {
        if (window.stageTimer) clearInterval(window.stageTimer);
        $("dash-loader").classList.add("hidden");
    }
    $("asof").textContent = "As of " + d.as_of;
    $("disclaimer").textContent = d.disclaimer;
    
    if (!silent) {
        if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
        window.autoRefreshTop = setInterval(() => {
            if (!$("dashboard").classList.contains("hidden")) {
                loadTop(true);
            } else {
                clearInterval(window.autoRefreshTop);
            }
        }, 15000);
    }
  } catch (e) {
    if (!silent) {
        if (window.stageTimer) clearInterval(window.stageTimer);
        $("dash-loader").textContent = "⚠ " + e.message;
    }
  }
}

function renderTable(id, rows, retKey, period) {
  const periodLabel = { "1d": "1D", "1w": "1W", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y" }[period] || period;
  const head = `<thead><tr>
      <th class="l">Stock</th><th>Price</th><th>Score</th><th>Signal</th>
      <th>${periodLabel}</th><th>Stop</th><th>Target</th>
    </tr></thead>`;
  const body = rows.map((r) => `
    <tr data-sym="${r.symbol}" title="${(r.reason || "").replace(/"/g, "'")}">
      <td class="l"><div class="sym">${r.symbol}</div><div class="muted">${(r.name || "").slice(0, 22)}</div></td>
      <td>${inr(r.price)}</td>
      <td><b>${r.score}</b></td>
      <td><span class="pill ${r.action}">${r.label}</span></td>
      <td>${pct(r[retKey])}</td>
      <td>${r.stop_loss ? inr(r.stop_loss) : "—"}</td>
      <td>${r.target1 ? inr(r.target1) : "—"}</td>
    </tr>`).join("");
  const el = $(id);
  el.innerHTML = head + "<tbody>" + (body || `<tr><td colspan="7" class="muted">No matches</td></tr>`) + "</tbody>";
  el.querySelectorAll("tbody tr[data-sym]").forEach((tr) =>
    tr.addEventListener("click", () => selectSymbol(tr.dataset.sym)));
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
$("horizon-toggle").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.horizon = b.dataset.h;
  $("horizon-toggle").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  renderRecommendation();
});

$("range-toggle").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.range = b.dataset.r;
  if (state.symbol) loadStock(state.symbol);
});

$("f-horizon").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  dash.horizon = b.dataset.h;
  $("f-horizon").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  // sensible default period per horizon
  loadTop();
});
$("f-period").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  dash.period = b.dataset.p;
  $("f-period").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  loadTop();
});
$("f-universe").addEventListener("change", (e) => { dash.universe = e.target.value; loadTop(); });
$("f-sort").addEventListener("change", (e) => { dash.sort = e.target.value; loadTop(); });
$("f-refresh").addEventListener("click", () => loadTop());
$("back-btn").addEventListener("click", () => go(DASH_HASH));

// ---------------------------------------------------------------------------
// Init — route to whatever the URL hash says (deep-linkable), default dashboard
// ---------------------------------------------------------------------------
(async function init() {
  try { const h = await api("/api/health"); $("asof").textContent = "As of " + h.as_of; } catch (e) { }
  route();
})();
