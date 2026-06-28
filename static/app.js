"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = { symbol: null, horizon: "short", range: "6mo", data: null };
const dash = { horizon: "short", period: "1w", universe: "nifty50", sort: "score", mode: "signals" };
let chart = null, chartObs = null, suggestItems = [], suggestActive = -1, searchTimer = null, searchAbort = null;
window.stageTimer = null;
window.autoRefresh = null;
window.autoRefreshTop = null;
window.candleSeries = null;
window.volSeries = null;
window.overlaySeries = {};
// chart editor state
window.showLevels = true;
window.drawTool = null;
window.pendingPoint = null;
window.levelLines = [];
window.drawnLines = [];
window.drawnSeries = [];
// config from backend
window.emailConfigured = false;
window.defaultEmail = "";

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
// Glossary — single source of truth for hover tooltips AND the Glossary tab.
// Plain-English explanations of every term/abbreviation used in the app.
// ---------------------------------------------------------------------------
const GLOSSARY = [
  { group: "The recommendation", items: [
    { k: "score", term: "Signal score (0–100)", short: "Our overall buy/sell rating, 0–100. Higher = more bullish.",
      long: "An overall 0–100 rating that blends trend, momentum, volume and (for long-term) fundamentals. Higher means more bullish. It's transparent, rule-based technical analysis — not financial advice." },
    { k: "action", term: "BUY / HOLD / SELL label", short: "STRONG BUY ≥75, BUY ≥60, HOLD 45–60, SELL 30–45, STRONG SELL <30.",
      long: "A plain label derived from the score: STRONG BUY (75+), BUY (60–74), HOLD (45–59), SELL (30–44), STRONG SELL (under 30)." },
    { k: "confidence", term: "Confidence", short: "How strongly and consistently the indicators agree.",
      long: "How decisive the signals are: higher when many indicators point the same way and the trend is strong. Low confidence means mixed/choppy signals." },
    { k: "winrate", term: "Historical win-rate", short: "How often this strategy's past BUY signals were profitable (backtest).",
      long: "A backtest over the past year: of the times these signals flashed BUY, how often was the price higher 20 trading days later. Past performance does not guarantee future results." },
    { k: "horizon", term: "Short-term vs Long-term", short: "Short = days–weeks (swing); Long = months–years (investing).",
      long: "Short-term weights momentum and fast moving averages for swing trades (days to weeks). Long-term weights the 200-day trend, golden/death cross and fundamentals for investing (months to years)." },
  ]},
  { group: "Trade levels", items: [
    { k: "entry", term: "Entry", short: "Suggested reference price to enter — the current live price.",
      long: "The reference price for the trade idea (the current live price). Where you'd consider getting in." },
    { k: "stop_loss", term: "Stop-loss", short: "Pre-set exit price to cap your loss if the trade goes wrong.",
      long: "A price decided in advance to sell and limit losses if the stock moves against you. We place it using ATR / recent support so normal wiggles don't trigger it." },
    { k: "target", term: "Target 1 / Target 2", short: "Prices to book profit (≈2× and 3× your risk).",
      long: "Profit-taking levels. Target 1 is about twice your risk and Target 2 about three times, where risk = the distance from entry to stop-loss." },
    { k: "risk_reward", term: "Risk : Reward", short: "Potential loss vs gain. 1:2 = risk ₹1 to make ₹2.",
      long: "Compares how much you'd lose if the stop-loss hits versus how much you'd gain at the target. 1:2 means the reward is twice the risk." },
    { k: "risk_pct", term: "Risk % / Risk per share", short: "How much you'd lose (per share / as %) if stopped out.",
      long: "The distance from entry to stop-loss, shown per share (₹) and as a percentage — your loss if the stop is hit." },
  ]},
  { group: "Momentum & trend indicators", items: [
    { k: "rsi", term: "RSI (Relative Strength Index)", short: "Momentum 0–100; >70 overbought, <30 oversold.",
      long: "Measures how fast and far the price moved recently, 0–100. Above 70 it may have risen too fast (overbought, could pull back); below 30 it may have fallen too far (oversold, could bounce). ~50 is neutral." },
    { k: "macd_hist", term: "MACD / MACD histogram", short: "Two moving averages compared; positive = upward momentum.",
      long: "MACD compares a fast and a slow moving average. When its line is above the 'signal' line (positive histogram), momentum is turning up (bullish); below, it's turning down (bearish)." },
    { k: "adx", term: "ADX (trend strength)", short: "How STRONG the trend is (not its direction). >25 = strong.",
      long: "Measures the strength of a trend regardless of direction. Below ~20 = choppy/sideways; above 25 = a strong trend. We pair it with +DI/−DI to know up or down." },
    { k: "stoch", term: "Stochastic %K", short: "Where price sits in its recent range, 0–100; >80 high, <20 low.",
      long: "Shows where the close sits within the recent high–low range, 0–100. Above 80 = near the top (overbought); below 20 = near the bottom (oversold)." },
    { k: "dma", term: "Moving average (DMA / SMA)", short: "Average closing price over N days; trend reference line.",
      long: "The average closing price over the last N days (20-DMA, 50-DMA, 200-DMA). Price above a rising average = uptrend. The 200-DMA is the classic long-term trend line." },
    { k: "golden_cross", term: "Golden cross / Death cross", short: "50-DMA crossing above 200-DMA (bullish) / below (bearish).",
      long: "When the 50-day average crosses above the 200-day average it's a 'golden cross' (long-term bullish). Crossing below is a 'death cross' (bearish)." },
    { k: "bollinger", term: "Bollinger Bands", short: "Bands around price; lower = stretched-cheap, upper = stretched-rich.",
      long: "A moving average with an upper and lower band 2 standard deviations away. Price near the lower band can be oversold; near the upper band, overbought/stretched." },
    { k: "atr", term: "ATR (Average True Range)", short: "Typical daily price move in ₹; used to size stop-losses.",
      long: "The average distance a stock travels in a day, in rupees. Bigger ATR = more volatile. We use it to set stop-losses far enough away to survive normal noise." },
  ]},
  { group: "Volume", items: [
    { k: "volume", term: "Volume", short: "Shares traded; high volume confirms a move.",
      long: "How many shares changed hands. A price move on high volume is more reliable; rising volume in an uptrend signals real demand." },
    { k: "obv", term: "OBV (On-Balance Volume)", short: "Running volume tally; rising = buying pressure (accumulation).",
      long: "Adds volume on up days and subtracts it on down days. Rising OBV means volume is flowing in on up days — a sign of quiet accumulation." },
    { k: "accum", term: "Accumulation", short: "Quiet buying: OBV rising and volume building, price climbing.",
      long: "Signs of buying before the crowd notices: on-balance volume rising and recent volume higher than before, while the price climbs steadily." },
  ]},
  { group: "Performance", items: [
    { k: "ret", term: "Return (1W / 1M / …)", short: "Percent price change over that period.",
      long: "How much the price changed over the period (1 week, 1 month, 3/6 months, 1 year), in percent." },
    { k: "price", term: "Price", short: "Latest live traded price (₹).",
      long: "The most recent live traded price in rupees, updated through the day." },
  ]},
  { group: "Fundamentals", items: [
    { k: "pe", term: "P/E (Price-to-Earnings)", short: "Price ÷ yearly profit per share; how pricey vs earnings.",
      long: "Share price divided by earnings per share — roughly how many years of current profit you're paying for. Lower can be cheaper, but compare within the same industry." },
    { k: "pb", term: "P/B (Price-to-Book)", short: "Price vs the company's net assets per share.",
      long: "Share price divided by book value (assets minus liabilities) per share. Below 1 means it trades under its accounting net worth." },
    { k: "roe", term: "ROE (Return on Equity)", short: "Profit per ₹ of shareholder money; higher is better.",
      long: "Profit as a percentage of shareholders' money. Higher ROE (e.g. >15%) means the company turns capital into profit efficiently." },
    { k: "div_yield", term: "Dividend yield", short: "Yearly dividend as a % of the share price.",
      long: "The annual dividend as a percentage of price. A 2% yield pays about ₹2 a year for every ₹100 invested." },
    { k: "market_cap", term: "Market cap", short: "Total company value = price × number of shares.",
      long: "The company's total market value (price × shares). Large-cap = big and stabler; small-cap = smaller and riskier. ₹1 L Cr = ₹1 lakh crore = ₹10,000 crore… (1 lakh crore)." },
    { k: "fiftytwo", term: "52-week High / Low", short: "Highest and lowest price over the past year.",
      long: "The highest and lowest the stock traded in the last 52 weeks — a quick sense of its range and where it sits now." },
    { k: "analyst_target", term: "Analyst target", short: "Average 12-month price brokerages expect.",
      long: "The average 12-month price target from professional analysts covering the stock — a rough consensus, not a guarantee." },
  ]},
  { group: "News", items: [
    { k: "sentiment", term: "News sentiment", short: "Tone of recent headlines, −1 (negative) to +1 (positive).",
      long: "We scan recent news headlines and score their tone from −1 (very negative) to +1 (very positive). A quick read of the news mood, not a price prediction." },
  ]},
  { group: "Steady-risers screener", items: [
    { k: "steady_score", term: "Steady score", short: "0–100 for quiet, smooth uptrends (pre-news accumulation).",
      long: "Ranks how steadily and quietly a stock is rising: a smooth trend, mostly up-days, low volatility, building volume, and not yet overextended — the kind of move that can precede the news." },
    { k: "smoothness", term: "Smoothness (R²)", short: "How straight-line the uptrend is, 0–1; higher = steadier.",
      long: "From a straight line fitted to recent prices: R² near 1 means price hugs a straight upward line (very steady); near 0 means choppy." },
    { k: "up_days", term: "Up-days", short: "% of recent days that closed higher.",
      long: "The percentage of recent trading days that closed up. Higher = more consistent climbing." },
    { k: "daily_vol", term: "Daily volatility", short: "Typical day-to-day swing; lower = calmer/steadier.",
      long: "How much the daily return jumps around. Lower means a calmer, steadier climb." },
    { k: "trend_mo", term: "Trend / month", short: "Estimated steady drift per month from the trend line.",
      long: "The slope of the fitted trend line, expressed as roughly how many percent it rises per month." },
  ]},
  { group: "Chart", items: [
    { k: "candlestick", term: "Candlestick", short: "Each bar = open/high/low/close; green up, red down.",
      long: "Each candle shows the open, high, low and close for the period. Green = closed higher than it opened; red = lower. The thin wicks mark the high and low." },
    { k: "sma_overlay", term: "SMA20 / SMA50 / SMA200 lines", short: "Moving-average lines overlaid on the chart.",
      long: "The coloured lines are 20-, 50- and 200-day average prices. Their slope and order show the trend (e.g. price above all three = strong uptrend)." },
  ]},
  { group: "Alerts", items: [
    { k: "direction", term: "Direction (long / short)", short: "Long = expecting up (target above); short = expecting down.",
      long: "Long means you expect the price to rise — target is above, stop below. Short means you expect a fall — target below, stop above. Alerts use this to decide which level was 'hit'." },
  ]},
];

const TIP = {};
GLOSSARY.forEach((g) => g.items.forEach((it) => { TIP[it.k] = it.short; }));

function tipAttr(key) {
  const s = TIP[key];
  return s ? ` data-tip="${s.replace(/"/g, "'")}"` : "";
}

// floating tooltip (appended to body so scroll containers never clip it)
const tipEl = document.createElement("div");
tipEl.className = "tip-pop hidden";
document.body.appendChild(tipEl);
function showTip(target) {
  const txt = target.getAttribute("data-tip");
  if (!txt) return;
  tipEl.textContent = txt;
  tipEl.classList.remove("hidden");
  const r = target.getBoundingClientRect();
  const tr = tipEl.getBoundingClientRect();
  let left = Math.max(8, Math.min(r.left + r.width / 2 - tr.width / 2, window.innerWidth - tr.width - 8));
  let top = r.top - tr.height - 8;
  if (top < 8) top = r.bottom + 8;
  tipEl.style.left = left + "px";
  tipEl.style.top = top + "px";
}
function hideTip() { tipEl.classList.add("hidden"); }
document.addEventListener("mouseover", (e) => {
  const t = e.target.closest("[data-tip]"); if (t) showTip(t);
});
document.addEventListener("mouseout", (e) => {
  const t = e.target.closest("[data-tip]");
  if (t && !t.contains(e.relatedTarget)) hideTip();
});
window.addEventListener("scroll", hideTip, true);

function renderGlossary() {
  $("glossary-content").innerHTML = GLOSSARY.map((g) => `
    <div class="gl-group">
      <h3>${g.group}</h3>
      ${g.items.map((it) => `
        <div class="gl-item">
          <div class="gl-term" data-tip="${it.short.replace(/"/g, "'")}">${it.term}</div>
          <div class="gl-desc">${it.long}</div>
        </div>`).join("")}
    </div>`).join("");
}

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
const ALERTS_HASH = "#/alerts";
const GLOSSARY_HASH = "#/glossary";
const PORTFOLIO_HASH = "#/portfolio";
const pf = { horizon: "long" };

function go(hash) {
  if (location.hash === hash) route();   // same hash -> force a re-route
  else location.hash = hash;             // otherwise hashchange fires route()
}

function hideAllViews() {
  $("detail").classList.add("hidden");
  $("detail-loader").classList.add("hidden");
  $("dashboard").classList.add("hidden");
  $("alerts-view").classList.add("hidden");
  $("glossary-view").classList.add("hidden");
  $("portfolio-view").classList.add("hidden");
}

function route() {
  const h = location.hash || "";
  if (h.startsWith(STOCK_HASH)) {
    const sym = decodeURIComponent(h.slice(STOCK_HASH.length)).toUpperCase();
    if (sym) openStock(sym); else openDashboard();
  } else if (h === ALERTS_HASH) {
    openAlertsView();
  } else if (h === GLOSSARY_HASH) {
    openGlossaryView();
  } else if (h === PORTFOLIO_HASH) {
    openPortfolioView();
  } else {
    openDashboard();
  }
}

function openPortfolioView() {
  if (window.autoRefresh) clearInterval(window.autoRefresh);
  if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
  hideAllViews();
  $("portfolio-view").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  loadPortfolio();
}

function openGlossaryView() {
  if (window.autoRefresh) clearInterval(window.autoRefresh);
  if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
  hideAllViews();
  $("glossary-view").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  renderGlossary();
}

function openStock(sym) {
  if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
  hideAllViews();
  searchEl.value = sym;
  window.scrollTo({ top: 0 });
  loadStock(sym);
}

function openDashboard() {
  state.symbol = null;
  if (window.autoRefresh) clearInterval(window.autoRefresh);
  if (window.stageTimer) clearInterval(window.stageTimer);
  hideAllViews();
  $("dashboard").classList.remove("hidden");
  searchEl.value = "";
  window.scrollTo({ top: 0 });
  if (dash.mode === "steady") loadSteady(); else loadTop();
}

function openAlertsView() {
  state.symbol = null;
  if (window.autoRefresh) clearInterval(window.autoRefresh);
  if (window.autoRefreshTop) clearInterval(window.autoRefreshTop);
  hideAllViews();
  $("alerts-view").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  loadAlerts();
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

  // explain the headline label on hover
  $("rec-label").setAttribute("data-tip", TIP.action || "");

  // Levels
  const lv = a.levels;
  const cells = [];
  if (lv) {
    cells.push({ k: "Entry", v: inr(lv.entry), tip: "entry" });
    cells.push({ k: a.action === "HOLD" ? "Protective stop" : "Stop-loss", v: inr(lv.stop_loss), c: "var(--red)", tip: "stop_loss" });
    if (lv.target1) cells.push({ k: "Target 1", v: inr(lv.target1), c: "var(--green)", tip: "target" });
    if (lv.target2) cells.push({ k: "Target 2", v: inr(lv.target2), c: "var(--green)", tip: "target" });
    if (!lv.target1) cells.push({ k: "Key level", v: inr(lv.key_level) });
    cells.push({ k: "Risk / share", v: inr(lv.risk_per_share), tip: "risk_pct" });
    cells.push({ k: "Risk %", v: fmt(lv.risk_pct) + "%", tip: "risk_pct" });
    if (lv.risk_reward) cells.push({ k: "Risk : Reward", v: "1 : " + lv.risk_reward, tip: "risk_reward" });
  }
  $("levels").innerHTML = cells.map((c) =>
    `<div class="level"><div class="lk"${tipAttr(c.tip)}>${c.k}</div><div class="lv" style="color:${c.c || "var(--text)"}">${c.v}</div></div>`
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

  // Chart editor: fresh chart -> reset drawings, draw levels, enable click-draw.
  window.levelLines = []; window.drawnLines = []; window.drawnSeries = []; window.pendingPoint = null;
  applyLevels();
  chart.subscribeClick(onChartClick);
  if (window.drawTool === "trend") chart.applyOptions({ handleScroll: false, handleScale: false });

  window.chartKey = chartKey;   // mark which stock+range this chart holds
}

// ---------------------------------------------------------------------------
// Chart editor: target/stop level lines, freehand drawing, PNG export
// ---------------------------------------------------------------------------
function applyLevels() {
  if (!chart || !window.candleSeries) return;
  (window.levelLines || []).forEach((pl) => {
    try { window.candleSeries.removePriceLine(pl); } catch (e) {}
  });
  window.levelLines = [];
  if (!window.showLevels) return;
  const a = state.data && state.data.analysis && state.data.analysis[state.horizon];
  const lv = a && a.levels;
  if (!lv) return;
  const defs = [
    { p: lv.entry, c: "#4c8dff", t: "Entry" },
    { p: lv.stop_loss, c: "#ff5765", t: a.action === "HOLD" ? "Protective stop" : "Stop-loss" },
    { p: lv.target1, c: "#2ecc71", t: "Target 1" },
    { p: lv.target2, c: "#1c9e57", t: "Target 2" },
  ];
  defs.forEach((d) => {
    if (d.p) window.levelLines.push(window.candleSeries.createPriceLine({
      price: d.p, color: d.c, lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: d.t,
    }));
  });
}

function onChartClick(param) {
  // Horizontal line: single click drops a line at that price.
  if (window.drawTool !== "hline" || !param.point || !chart || !window.candleSeries) return;
  const price = window.candleSeries.coordinateToPrice(param.point.y);
  if (price == null) return;
  const pl = window.candleSeries.createPriceLine({
    price, color: "#e6ebf5", lineWidth: 1, lineStyle: 0,
    axisLabelVisible: true, title: "",
  });
  window.drawnLines.push(pl);
}

// Trendline: click-drag from one point to another (more reliable than 2 clicks).
let _dragStart = null;
function _pointToTP(e) {
  if (!chart || !window.candleSeries) return null;
  const rect = $("chart").getBoundingClientRect();
  const time = chart.timeScale().coordinateToTime(e.clientX - rect.left);
  const value = window.candleSeries.coordinateToPrice(e.clientY - rect.top);
  return (time == null || value == null) ? null : { time, value };
}
$("chart").addEventListener("pointerdown", (e) => {
  if (window.drawTool !== "trend") return;
  _dragStart = _pointToTP(e);
});
$("chart").addEventListener("pointerup", (e) => {
  if (window.drawTool !== "trend" || !_dragStart) return;
  const end = _pointToTP(e);
  const start = _dragStart; _dragStart = null;
  if (!end || start.time === end.time) { setHint("Drag a bit further to draw a line."); return; }
  const pts = [start, end].sort((a, b) => (a.time > b.time ? 1 : -1));
  const ls = chart.addLineSeries({
    color: "#e6ebf5", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
  });
  ls.setData(pts);
  window.drawnSeries.push(ls);
  setHint("Trendline added — drag again for another.");
});

function clearDrawings() {
  (window.drawnLines || []).forEach((pl) => {
    try { window.candleSeries.removePriceLine(pl); } catch (e) {}
  });
  (window.drawnSeries || []).forEach((s) => {
    try { chart.removeSeries(s); } catch (e) {}
  });
  window.drawnLines = []; window.drawnSeries = []; window.pendingPoint = null;
  setHint("Drawings cleared.");
}

function downloadChart() {
  if (!chart || !chart.takeScreenshot) return;
  const canvas = chart.takeScreenshot();
  const a = document.createElement("a");
  a.download = `${state.symbol || "chart"}_${state.range}.png`;
  a.href = canvas.toDataURL("image/png");
  document.body.appendChild(a); a.click(); a.remove();
}

function setHint(msg) {
  const el = $("ct-hint"); if (el) el.textContent = msg || "";
}

// ---------------------------------------------------------------------------
// Stats grid
// ---------------------------------------------------------------------------
function renderStats() {
  const i = state.data.indicators, f = state.data.fundamentals, q = state.data.quote;
  const rsiTag = i.rsi14 == null ? "" :
    i.rsi14 >= 70 ? ' <small class="down">OB</small>' : i.rsi14 <= 30 ? ' <small class="up">OS</small>' : "";

  const stats = [
    ["News Sentiment", state.data.sentiment?.articles ? `${state.data.sentiment.score > 0 ? "+" : ""}${state.data.sentiment.score} <small>(${state.data.sentiment.articles} articles)</small>` : "—", "sentiment"],
    ["RSI (14)", i.rsi14 == null ? "—" : fmt(i.rsi14, 0) + rsiTag, "rsi"],
    ["MACD hist", i.macd_hist == null ? "—" : `<span class="${i.macd_hist >= 0 ? "up" : "down"}">${fmt(i.macd_hist, 2)}</span>`, "macd_hist"],
    ["ADX (trend)", i.adx == null ? "—" : fmt(i.adx, 0), "adx"],
    ["Stochastic %K", i.stoch_k == null ? "—" : fmt(i.stoch_k, 0), "stoch"],
    ["ATR (14)", i.atr14 == null ? "—" : `${inr(i.atr14)} <small>(${fmt(i.atr_pct, 1)}%)</small>`, "atr"],
    ["20-DMA", inr(i.sma20), "dma"],
    ["50-DMA", inr(i.sma50), "dma"],
    ["200-DMA", inr(i.sma200), "dma"],
    ["Return 1W", pct(i.ret_1w), "ret"],
    ["Return 1M", pct(i.ret_1m), "ret"],
    ["Return 3M", pct(i.ret_3m), "ret"],
    ["Return 6M", pct(i.ret_6m), "ret"],
    ["Return 1Y", pct(i.ret_1y), "ret"],
    ["Volume", i.vol == null ? "—" : Number(i.vol).toLocaleString("en-IN"), "volume"],
    ["P/E", f.pe == null ? "—" : fmt(f.pe, 1), "pe"],
    ["P/B", f.pb == null ? "—" : fmt(f.pb, 1), "pb"],
    ["ROE", f.roe == null ? "—" : fmt(f.roe * 100, 1) + "%", "roe"],
    ["Div yield", f.dividend_yield == null ? "—" : fmt(f.dividend_yield * (f.dividend_yield < 1 ? 100 : 1), 2) + "%", "div_yield"],
    ["Market cap", crore(q.market_cap), "market_cap"],
    ["52W High", inr(q.fifty_two_high), "fiftytwo"],
    ["52W Low", inr(q.fifty_two_low), "fiftytwo"],
    ["Analyst target", f.target_mean == null ? "—" : inr(f.target_mean), "analyst_target"],
  ];
  $("stats-grid").innerHTML = stats.map(([k, v, tip]) =>
    `<div class="stat"><div class="sk"${tipAttr(tip)}>${k} <span class="info">ⓘ</span></div><div class="sv">${v}</div></div>`).join("");
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
      <th class="l">Stock</th><th${tipAttr("price")}>Price</th><th${tipAttr("score")}>Score</th>
      <th${tipAttr("action")}>Signal</th><th${tipAttr("ret")}>${periodLabel}</th>
      <th${tipAttr("stop_loss")}>Stop</th><th${tipAttr("target")}>Target</th>
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
// Dashboard mode + steady-accumulation screener
// ---------------------------------------------------------------------------
function setDashMode(mode) {
  dash.mode = mode;
  $("dash-mode").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.m === mode));
  const steady = mode === "steady";
  $("steady-wrap").classList.toggle("hidden", !steady);
  $("signal-tables").classList.toggle("hidden", steady);
  ["f-horizon", "f-period", "f-sort"].forEach((id) => {
    const e = $(id); if (e) e.style.display = steady ? "none" : "";
  });
  if (steady) loadSteady(); else loadTop();
}

async function loadSteady(silent = false) {
  if (!silent) {
    if (window.stageTimer) clearInterval(window.stageTimer);
    $("dash-loader").classList.remove("hidden");
    $("dash-loader").innerHTML =
      `<div class="spinner" style="display:inline-block;vertical-align:middle;margin-right:8px;width:16px;height:16px;border-width:2px"></div>` +
      `<span>Scanning for steady, quiet uptrends…</span>`;
  }
  try {
    const d = await api(`/api/steady?universe=${dash.universe}&limit=20`);
    renderSteady(d.rows || []);
    if (!silent) $("dash-loader").classList.add("hidden");
    if (d.as_of) $("asof").textContent = "As of " + d.as_of;
    if (d.disclaimer) $("disclaimer").textContent = d.disclaimer;
  } catch (e) {
    if (!silent) { $("dash-loader").classList.remove("hidden"); $("dash-loader").textContent = "⚠ " + e.message; }
  }
}

function renderSteady(rows) {
  const head = `<thead><tr>
      <th class="l">Stock</th><th${tipAttr("price")}>Price</th><th${tipAttr("steady_score")}>Steady</th>
      <th${tipAttr("trend_mo")}>Trend/mo</th><th${tipAttr("smoothness")}>Smoothness</th>
      <th${tipAttr("up_days")}>Up-days</th><th${tipAttr("daily_vol")}>Daily vol</th><th${tipAttr("accum")}>Accum</th>
    </tr></thead>`;
  const body = rows.map((r) => `
    <tr data-sym="${r.symbol}" title="${(r.reason || "").replace(/"/g, "'")}">
      <td class="l"><div class="sym">${r.symbol}</div><div class="muted">${(r.name || "").slice(0, 22)}</div></td>
      <td>${inr(r.price)}</td>
      <td><b>${r.steady_score}</b> <span class="bar" style="width:${Math.max(4, r.steady_score / 2)}px"></span></td>
      <td>${pct(r.slope_pct_month)}</td>
      <td>${fmt(r.r2, 2)}</td>
      <td>${fmt(r.up_days_pct, 0)}%</td>
      <td>${fmt(r.daily_vol_pct, 1)}%</td>
      <td>${r.obv_up ? '<span class="up">↑</span>' : '<span class="muted">→</span>'}${r.vol_ratio > 1.1 ? ' <small class="up">vol↑</small>' : ""}</td>
    </tr>`).join("");
  const el = $("tbl-steady");
  el.innerHTML = head + "<tbody>" + (body || `<tr><td colspan="8" class="muted">No steady risers found right now</td></tr>`) + "</tbody>";
  el.querySelectorAll("tbody tr[data-sym]").forEach((tr) =>
    tr.addEventListener("click", () => selectSymbol(tr.dataset.sym)));
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------
async function loadAlerts() {
  $("alerts-list").innerHTML = '<div class="loader">Loading alerts…</div>';
  try {
    const d = await api("/api/alerts");
    window.emailConfigured = d.email_configured;
    $("alerts-hint").innerHTML = d.email_configured
      ? "✅ Email is configured — active alerts are checked automatically every few minutes."
      : "⚠️ Email isn't configured on the server yet. Alerts will still trigger and show here, but no email is sent until <code>SMTP_USER</code> / <code>SMTP_PASS</code> are set (see README).";
    renderAlerts(d.alerts || []);
  } catch (e) {
    $("alerts-list").innerHTML = '<div class="loader">⚠ ' + e.message + "</div>";
  }
}

function renderAlerts(alerts) {
  if (!alerts.length) {
    $("alerts-list").innerHTML = '<div class="muted">No alerts yet. Open a stock and tap “🔔 Set alert”.</div>';
    return;
  }
  $("alerts-list").innerHTML = alerts.map((a) => {
    const parts = [];
    if (a.target != null) parts.push(`🎯 Target ₹${fmt(a.target)}`);
    if (a.stop != null) parts.push(`🛑 Stop ₹${fmt(a.stop)}`);
    const status = a.status === "triggered"
      ? `<span class="a-status triggered">✓ ${("" + a.triggered_kind).toUpperCase()} hit @ ₹${fmt(a.triggered_price)}</span>`
      : `<span class="a-status active">● active (${a.direction})</span>`;
    return `<div class="alert-row">
      <div class="a-sym" data-sym="${a.symbol}">${a.symbol}</div>
      <div class="a-levels">${parts.join(" · ")}${a.note ? ` — <i>${a.note}</i>` : ""}
        <div class="muted">${a.email} · created ${a.created}</div></div>
      ${status}
      <button class="alert-del" data-id="${a.id}">Delete</button>
    </div>`;
  }).join("");
  $("alerts-list").querySelectorAll(".a-sym").forEach((el) =>
    el.addEventListener("click", () => selectSymbol(el.dataset.sym)));
  $("alerts-list").querySelectorAll(".alert-del").forEach((el) =>
    el.addEventListener("click", async () => {
      await fetch("/api/alerts/" + el.dataset.id, { method: "DELETE" });
      loadAlerts();
    }));
}

function openAlertModal() {
  if (!state.data) return;
  const a = state.data.analysis[state.horizon];
  const lv = (a && a.levels) || {};
  $("am-title").textContent = `Set alert — ${state.data.symbol}`;
  $("am-sub").textContent =
    `${state.data.name} · live ₹${fmt(state.data.quote.price)} · ${a ? a.action : ""} (${state.horizon}-term)`;
  $("am-target").value = lv.target1 != null ? lv.target1 : "";
  $("am-stop").value = lv.stop_loss != null ? lv.stop_loss : "";
  $("am-email").value = window.defaultEmail || "";
  $("am-note").value = "";
  $("am-msg").textContent = ""; $("am-msg").className = "am-msg";
  $("alert-modal").classList.remove("hidden");
}

function closeAlertModal() { $("alert-modal").classList.add("hidden"); }

async function saveAlert() {
  if (!state.data) return;
  const a = state.data.analysis[state.horizon];
  const payload = {
    symbol: state.data.symbol, name: state.data.name,
    target: $("am-target").value || null, stop: $("am-stop").value || null,
    email: $("am-email").value, note: $("am-note").value,
    direction: (a && a.action === "SELL") ? "short" : "long",
  };
  const msg = $("am-msg");
  msg.className = "am-msg"; msg.textContent = "Saving…";
  try {
    const r = await fetch("/api/alerts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!d.ok) { msg.className = "am-msg err"; msg.textContent = "⚠ " + (d.error || "Failed"); return; }
    msg.className = "am-msg ok"; msg.textContent = "✓ Alert created. You'll be emailed when it hits.";
    window.defaultEmail = payload.email;
    setTimeout(closeAlertModal, 1000);
  } catch (e) { msg.className = "am-msg err"; msg.textContent = "⚠ " + e.message; }
}

// ---------------------------------------------------------------------------
// Portfolio
// ---------------------------------------------------------------------------
const verdictClass = { good: "v-good", neutral: "v-neutral", warn: "v-warn", bad: "v-bad" };

async function loadPortfolio() {
  $("pf-loader").classList.remove("hidden");
  try {
    const d = await api(`/api/portfolio?horizon=${pf.horizon}`);
    renderPortfolioSummary(d.summary);
    renderPortfolioTable(d.holdings || []);
    if (d.as_of) $("asof").textContent = "As of " + d.as_of;
    $("pf-note").innerHTML = (d.holdings && d.holdings.length)
      ? "Stop-loss & target are <b>protective levels for a long position</b> (stop below price, target above). “Entry” rates your buy point as of your buy date. Educational analysis — not investment advice."
      : "Your portfolio is empty — use <b>+ Add holding</b> or <b>⤓ Import</b> your tradebook.";
  } catch (e) {
    $("tbl-portfolio").innerHTML = `<tbody><tr><td class="muted">⚠ ${e.message}</td></tr></tbody>`;
  } finally {
    $("pf-loader").classList.add("hidden");
  }
}

function renderPortfolioSummary(s) {
  if (!s) { $("pf-summary").innerHTML = ""; return; }
  const pnlCls = (s.pnl ?? 0) >= 0 ? "up" : "down";
  const cards = [
    { k: "Holdings", v: s.count },
    { k: "Invested", v: inr(s.invested) },
    { k: "Current value", v: inr(s.current_value) },
    { k: "Total P&L", v: `<span class="${pnlCls}">${inr(s.pnl)} <small>(${s.pnl_pct == null ? "—" : (s.pnl_pct >= 0 ? "+" : "") + fmt(s.pnl_pct) + "%"})</small></span>` },
  ];
  $("pf-summary").innerHTML = cards.map((c) =>
    `<div class="pf-card"><div class="pf-k">${c.k}</div><div class="pf-v">${c.v}</div></div>`).join("");
}

function renderPortfolioTable(rows) {
  const head = `<thead><tr>
    <th class="l">Stock</th><th>Qty</th><th>Avg cost</th><th>LTP</th><th>P&amp;L</th>
    <th${tipAttr("action")}>Signal</th><th${tipAttr("stop_loss")}>Stop</th><th${tipAttr("target")}>Target</th>
    <th>Entry</th><th class="l">What to do</th><th></th>
  </tr></thead>`;
  const body = rows.map((a) => {
    const pnlCls = (a.pnl ?? 0) >= 0 ? "up" : "down";
    const eq = a.entry_quality;
    const eqBadge = eq
      ? `<span class="eq eq-${eq.quality}" data-tip="${(eq.notes || []).join('; ').replace(/"/g, "'")}">${eq.quality}</span>`
      : '<span class="muted">—</span>';
    const v = a.verdict || {};
    return `<tr data-sym="${a.symbol}">
      <td class="l"><div class="sym">${a.symbol}</div><div class="muted">${(a.name || '').slice(0, 20)}</div></td>
      <td>${fmt(a.qty, 0)}</td>
      <td>${inr(a.avg_price)}</td>
      <td>${a.current_price != null ? inr(a.current_price) : '—'}</td>
      <td class="${pnlCls}">${a.pnl != null ? inr(a.pnl) : '—'}<div class="muted">${a.pnl_pct != null ? (a.pnl_pct >= 0 ? '+' : '') + fmt(a.pnl_pct) + '%' : ''}</div></td>
      <td>${a.label ? `<span class="pill ${a.action}">${a.label}</span>` : '—'}</td>
      <td>${a.stop_loss != null ? inr(a.stop_loss) : '—'}</td>
      <td>${a.target != null ? inr(a.target) : '—'}</td>
      <td>${eqBadge}</td>
      <td class="l"><span class="vbadge ${verdictClass[v.tone] || ''}">${v.text || '—'}</span></td>
      <td><button class="pf-del" data-id="${a.id}" title="Remove">×</button></td>
    </tr>`;
  }).join("");
  const el = $("tbl-portfolio");
  el.innerHTML = head + "<tbody>" + (body || `<tr><td colspan="11" class="muted">No holdings yet.</td></tr>`) + "</tbody>";
  el.querySelectorAll("tbody tr[data-sym]").forEach((tr) =>
    tr.addEventListener("click", (e) => { if (!e.target.closest(".pf-del")) selectSymbol(tr.dataset.sym); }));
  el.querySelectorAll(".pf-del").forEach((btn) =>
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch("/api/portfolio/" + btn.dataset.id, { method: "DELETE" });
      loadPortfolio();
    }));
}

async function savePfHolding() {
  const sym = $("pf-sym").value.trim().toUpperCase();
  const qty = $("pf-qty").value, avg = $("pf-avg").value, date = $("pf-date").value;
  const msg = $("pf-add-msg");
  if (!sym || !qty || !avg) { msg.textContent = "Symbol, qty and avg price are required."; return; }
  msg.textContent = "Adding…";
  try {
    const r = await fetch("/api/portfolio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: sym, qty, avg_price: avg, buy_date: date }),
    });
    const d = await r.json();
    if (!d.ok) { msg.textContent = "⚠ " + (d.error || "Failed"); return; }
    $("pf-sym").value = $("pf-qty").value = $("pf-avg").value = $("pf-date").value = "";
    msg.textContent = "";
    $("pf-add-form").classList.add("hidden");
    loadPortfolio();
  } catch (e) { msg.textContent = "⚠ " + e.message; }
}

function openImportModal() { $("import-msg").textContent = ""; $("import-msg").className = "am-msg"; $("import-modal").classList.remove("hidden"); }
function closeImportModal() { $("import-modal").classList.add("hidden"); }
async function doImport() {
  const msg = $("import-msg");
  let text = $("import-text").value;
  const file = $("import-file").files[0];
  msg.className = "am-msg"; msg.textContent = "Importing…";
  try {
    if (file && !text.trim()) text = await file.text();
    const r = await fetch("/api/portfolio/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    const d = await r.json();
    if (!d.ok) { msg.className = "am-msg err"; msg.textContent = "⚠ " + (d.error || "Failed"); return; }
    msg.className = "am-msg ok";
    msg.textContent = `✓ Imported ${d.added} holding(s)${d.skipped ? ` (skipped ${d.skipped})` : ""} from your ${d.kind}.`;
    $("import-text").value = ""; $("import-file").value = "";
    setTimeout(() => { closeImportModal(); loadPortfolio(); }, 1100);
  } catch (e) { msg.className = "am-msg err"; msg.textContent = "⚠ " + e.message; }
}

function openKotakModal() {
  $("kt-msg").textContent = ""; $("kt-msg").className = "am-msg";
  closeImportModal();
  $("kotak-modal").classList.remove("hidden");
}
function closeKotakModal() { $("kotak-modal").classList.add("hidden"); }
async function doKotakImport() {
  const msg = $("kt-msg");
  const body = {
    broker: "kotak",
    consumer_key: $("kt-key").value.trim(),
    mobile_number: $("kt-mobile").value.trim(),
    ucc: $("kt-ucc").value.trim(),
    totp: $("kt-totp").value.trim(),
    mpin: $("kt-mpin").value.trim(),
  };
  if (!body.consumer_key || (!body.mobile_number && !body.ucc) || !body.totp || !body.mpin) {
    msg.className = "am-msg err"; msg.textContent = "Fill consumer key, mobile/UCC, TOTP and MPIN."; return;
  }
  msg.className = "am-msg"; msg.textContent = "Connecting to Kotak Neo…";
  try {
    const r = await fetch("/api/portfolio/broker", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) { msg.className = "am-msg err"; msg.textContent = "⚠ " + (d.error || "Failed"); return; }
    msg.className = "am-msg ok"; msg.textContent = `✓ Imported ${d.added} of ${d.fetched} holding(s) from Kotak Neo.`;
    $("kt-totp").value = ""; $("kt-mpin").value = "";  // wipe sensitive fields
    setTimeout(() => { closeKotakModal(); loadPortfolio(); }, 1200);
  } catch (e) { msg.className = "am-msg err"; msg.textContent = "⚠ " + e.message; }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
$("horizon-toggle").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.horizon = b.dataset.h;
  $("horizon-toggle").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  renderRecommendation();
  applyLevels();  // refresh target/stop lines for the new horizon
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
$("f-universe").addEventListener("change", (e) => {
  dash.universe = e.target.value;
  if (dash.mode === "steady") loadSteady(); else loadTop();
});
$("f-sort").addEventListener("change", (e) => { dash.sort = e.target.value; loadTop(); });
$("f-refresh").addEventListener("click", () => { if (dash.mode === "steady") loadSteady(); else loadTop(); });
$("back-btn").addEventListener("click", () => go(DASH_HASH));

// dashboard mode (Top picks <-> Steady risers)
$("dash-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  setDashMode(b.dataset.m);
});

// chart editor toolbar
$("chart-toolbar").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "ct-clear") { clearDrawings(); return; }
  if (b.id === "ct-download") { downloadChart(); return; }
  const tool = b.dataset.tool;
  if (tool === "levels") {
    window.showLevels = !window.showLevels;
    b.classList.toggle("active", window.showLevels);
    applyLevels();
    return;
  }
  const newTool = (window.drawTool === tool) ? null : tool;
  window.drawTool = newTool;
  ["trend", "hline"].forEach((t) => {
    const el = document.querySelector(`#chart-toolbar [data-tool="${t}"]`);
    if (el) el.classList.toggle("active", t === newTool);
  });
  // disable pan/zoom while drawing a trendline so the drag draws instead of pans
  if (chart) chart.applyOptions({ handleScroll: newTool !== "trend", handleScale: newTool !== "trend" });
  setHint(newTool === "trend" ? "Drag across the chart to draw a trendline."
        : newTool === "hline" ? "Click on the chart to drop a horizontal line."
        : "");
});

// alerts + glossary navigation + modal
$("alerts-btn").addEventListener("click", () => go(ALERTS_HASH));
$("alerts-back").addEventListener("click", () => go(DASH_HASH));
$("glossary-btn").addEventListener("click", () => go(GLOSSARY_HASH));
$("glossary-back").addEventListener("click", () => go(DASH_HASH));

// portfolio
$("portfolio-btn").addEventListener("click", () => go(PORTFOLIO_HASH));
$("portfolio-back").addEventListener("click", () => go(DASH_HASH));
$("pf-horizon").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  pf.horizon = b.dataset.h;
  $("pf-horizon").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  loadPortfolio();
});
$("pf-add-btn").addEventListener("click", () => $("pf-add-form").classList.toggle("hidden"));
$("pf-save").addEventListener("click", savePfHolding);
$("pf-refresh").addEventListener("click", loadPortfolio);
$("pf-clear").addEventListener("click", async () => {
  if (confirm("Remove ALL holdings from your portfolio?")) {
    await fetch("/api/portfolio/clear", { method: "POST" });
    loadPortfolio();
  }
});
$("pf-import-btn").addEventListener("click", openImportModal);
$("import-cancel").addEventListener("click", closeImportModal);
$("import-save").addEventListener("click", doImport);
$("import-modal").addEventListener("click", (e) => { if (e.target.id === "import-modal") closeImportModal(); });
$("open-kotak").addEventListener("click", openKotakModal);
$("kt-cancel").addEventListener("click", closeKotakModal);
$("kt-save").addEventListener("click", doKotakImport);
$("kotak-modal").addEventListener("click", (e) => { if (e.target.id === "kotak-modal") closeKotakModal(); });
$("set-alert-btn").addEventListener("click", openAlertModal);
$("am-cancel").addEventListener("click", closeAlertModal);
$("am-save").addEventListener("click", saveAlert);
$("alert-modal").addEventListener("click", (e) => { if (e.target.id === "alert-modal") closeAlertModal(); });

// ---------------------------------------------------------------------------
// Init — route to whatever the URL hash says (deep-linkable), default dashboard
// ---------------------------------------------------------------------------
(async function init() {
  try { const h = await api("/api/health"); $("asof").textContent = "As of " + h.as_of; } catch (e) { }
  try {
    const c = await api("/api/config");
    window.emailConfigured = c.email_configured;
    window.defaultEmail = c.default_email || "";
  } catch (e) { }
  route();
})();
