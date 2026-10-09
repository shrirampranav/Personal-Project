/* Options Signal Desk front end: scanner, charts, checklist, alerts and paper journal. */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 2) => (n === null || n === undefined || Number.isNaN(n)) ? "-" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const STATUS_TEXT = {
    BUY_CALL: "BUY CALL", BUY_PUT: "BUY PUT", WATCH_CALL: "WATCH CALL", WATCH_PUT: "WATCH PUT",
    NO_TRADE: "NO TRADE", WAIT: "WAIT", EXIT_WINDOW: "NO ENTRY", LOCKED: "LOCKED", CLOSED: "CLOSED", ERROR: "ERROR",
  };

  const state = { config: null, selected: null, data: null, rows: [], fitted: null, notify: false, sound: true, loading: false };

  // ---------- charts ----------
  const LC = window.LightweightCharts;
  const baseOpts = () => ({
    autoSize: true,
    layout: { background: { type: "solid", color: css("--panel") }, textColor: css("--muted"), fontSize: 11 },
    grid: { vertLines: { color: css("--border") + "55" }, horzLines: { color: css("--border") + "55" } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  });
  const up = css("--up"), down = css("--down");

  const c5 = LC.createChart($("chart5"), baseOpts());
  const candles = c5.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
  const volume = c5.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
  c5.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const lineOpts = (color, width = 2) => ({ color, lineWidth: width, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
  const vwapS = c5.addLineSeries(lineOpts(css("--vwap")));
  const ema9S = c5.addLineSeries(lineOpts(css("--ema9"), 1));
  const ema21S = c5.addLineSeries(lineOpts(css("--ema21"), 1));
  const stUp = c5.addLineSeries(lineOpts(up));
  const stDn = c5.addLineSeries(lineOpts(down));
  let priceLines = [];

  const cr = LC.createChart($("chartRsi"), { ...baseOpts(), timeScale: { ...baseOpts().timeScale, visible: false } });
  const rsiS = cr.addLineSeries({ ...lineOpts(css("--accent"), 1.5), lastValueVisible: true });
  rsiS.createPriceLine({ price: 60, color: up, lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
  rsiS.createPriceLine({ price: 40, color: down, lineStyle: 2, lineWidth: 1, axisLabelVisible: true });

  // keep the RSI pane aligned with the price chart
  let syncing = false;
  const sync = (from, to) => from.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (syncing || !r) return; syncing = true; to.timeScale().setVisibleLogicalRange(r); syncing = false;
  });
  sync(c5, cr); sync(cr, c5);

  const c15 = LC.createChart($("chart15"), baseOpts());
  const candles15 = c15.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
  const vwap15 = c15.addLineSeries(lineOpts(css("--vwap"), 1));
  const ema15 = c15.addLineSeries(lineOpts(css("--ema21"), 1));
  const st15Up = c15.addLineSeries(lineOpts(up, 1));
  const st15Dn = c15.addLineSeries(lineOpts(down, 1));

  const splitST = (pts, dir) => pts.map((p) => (p.dir === dir ? { time: p.time, value: p.value } : { time: p.time }));

  function drawCharts(d) {
    const ch = d.chart;
    candles.setData(ch.candles);
    const isStock = d.kind === "stock";
    volume.setData(isStock ? ch.volume.map((v) => ({ time: v.time, value: v.value, color: (v.up ? up : down) + "66" })) : []);
    vwapS.setData(ch.vwap); ema9S.setData(ch.ema9); ema21S.setData(ch.ema21);
    stUp.setData(splitST(ch.supertrend, 1)); stDn.setData(splitST(ch.supertrend, -1));
    rsiS.setData(ch.rsi);

    priceLines.forEach((l) => candles.removePriceLine(l));
    priceLines = [];
    const lv = d.levels || {};
    const cprColor = css("--cpr");
    for (const k of ["R2", "R1", "TC", "P", "BC", "S1", "S2"]) {
      if (lv[k] === undefined) continue;
      const isCpr = ["TC", "P", "BC"].includes(k);
      priceLines.push(candles.createPriceLine({
        price: lv[k], title: k, lineWidth: 1, lineStyle: isCpr ? 2 : 1,
        color: isCpr ? cprColor : (k.startsWith("R") ? down + "99" : up + "99"), axisLabelVisible: false,
      }));
    }
    if (d.chain) {
      priceLines.push(candles.createPriceLine({ price: d.chain.resistance, title: "Max CE OI", color: css("--warn"), lineWidth: 1, lineStyle: 3, axisLabelVisible: false }));
      priceLines.push(candles.createPriceLine({ price: d.chain.support, title: "Max PE OI", color: css("--warn"), lineWidth: 1, lineStyle: 3, axisLabelVisible: false }));
    }
    const last = ch.candles[ch.candles.length - 1];
    const markers = [];
    if (d.status === "BUY_CALL" && last) markers.push({ time: last.time, position: "belowBar", color: up, shape: "arrowUp", text: "CALL" });
    if (d.status === "BUY_PUT" && last) markers.push({ time: last.time, position: "aboveBar", color: down, shape: "arrowDown", text: "PUT" });
    candles.setMarkers(markers);

    const m = ch.m15;
    candles15.setData(m.candles); ema15.setData(m.ema21); vwap15.setData(m.vwap);
    st15Up.setData(splitST(m.supertrend, 1)); st15Dn.setData(splitST(m.supertrend, -1));

    if (state.fitted !== d.symbol) {
      const n = ch.candles.length;
      c5.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 90), to: n + 3 });
      c15.timeScale().fitContent();
      state.fitted = d.symbol;
    }
  }

  // ---------- rendering ----------
  function renderScan() {
    const body = $("scanBody");
    body.innerHTML = state.rows.map((r) => {
      const chg = r.change_pct;
      return `<tr data-sym="${esc(r.symbol)}" class="${r.symbol === state.selected ? "sel" : ""}" title="${esc(r.message || "")}">
        <td class="sym">${esc(r.symbol)}</td>
        <td class="num">${fmt(r.ltp)}</td>
        <td class="num ${chg >= 0 ? "up" : "down"}">${chg === undefined || chg === null ? "-" : (chg >= 0 ? "+" : "") + fmt(chg) + "%"}</td>
        <td><span class="pill ${esc(r.status)}">${esc(STATUS_TEXT[r.status] || r.status)}</span></td>
        <td><button class="rm" data-rm="${esc(r.symbol)}" title="Remove">&times;</button></td></tr>`;
    }).join("");
  }

  function tick(on, bear) { return `<span class="tick ${on ? "yes" : "no"} ${bear ? "bear" : ""}">${on ? "&#10003;" : "&#10005;"}</span>`; }

  function renderSignal(d) {
    $("symName").textContent = d.symbol + (d.expiry_day ? "  (expiry day)" : "");
    $("symLtp").textContent = fmt(d.ltp);
    const chg = $("symChg");
    chg.textContent = (d.change_pct >= 0 ? "+" : "") + fmt(d.change_pct) + "%";
    chg.className = "chg " + (d.change_pct >= 0 ? "up" : "down");
    const pill = $("statusPill");
    pill.textContent = STATUS_TEXT[d.status] || d.status;
    pill.className = "status " + d.status;
    $("banner").className = "card banner " + d.status;
    $("scoreTxt").textContent = `Call ${d.score.bull}/6 · Put ${d.score.bear}/6 · candle ${d.time.slice(11)}`;
    $("statusMsg").textContent = d.message;
    $("trend15").textContent = d.trend15.toUpperCase();
    $("trend15").className = d.trend15 === "up" ? "up" : d.trend15 === "down" ? "down" : "muted";
    $("phaseChip").textContent = d.phase_msg;

    $("checkBody").innerHTML = d.checks.map((c) => `<tr><td>${esc(c.label)}<span class="val">${esc(c.value)}</span></td>
      <td class="c">${tick(c.bull)}</td><td class="c">${tick(c.bear, true)}</td></tr>`).join("");
    $("phaseRow").textContent = `Session: ${d.phase_msg}`;
    renderPlan(d);

    const rules = [
      "Stop: Supertrend line or last swing, or 25-30% of premium, whichever first.",
      "Target 1.5-2x risk; trail with Supertrend once in profit.",
      `Risk max ${state.config.risk_per_trade_pct}% of capital per trade; stop after ${state.config.max_losing_trades} losing trades.`,
      "Avoid 9:15-9:30. Best 9:30-11:30 and breakouts after 1 PM. Exit by 2:30 PM.",
    ];
    $("warnList").innerHTML = d.warnings.map((w) => `<li>${esc(w)}</li>`).join("") + rules.map((r) => `<li class="rule">${esc(r)}</li>`).join("");
  }

  function kv(pairs) { return pairs.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join(""); }

  function renderPlan(d) {
    const p = d.plan;
    const box = $("planBody");
    if (!p) { box.className = "muted"; box.innerHTML = "No setup. Plan appears when 5 or 6 conditions align."; return; }
    const o = p.option;
    const live = d.status.startsWith("BUY_");
    box.className = "";
    box.innerHTML = `
      <div class="plan-title ${p.direction === "CALL" ? "up" : "down"}">${live ? "" : "Forming: "}${esc(o.contract)}</div>
      <div class="kv-grid">${kv([
        ["Spot entry", fmt(p.entry)], ["Stop (spot)", fmt(p.stop)],
        ["Target 1 (1.5R)", fmt(p.targets[0])], ["Target 2 (2R)", fmt(p.targets[1])],
        ["Risk pts", fmt(p.risk_points)], ["Alt stop (swing)", fmt(p.alt_stop)],
      ])}</div>
      <div class="plan-block">
        <div class="muted" style="font-size:12px">${esc(o.strike_note)} · est. delta ${o.delta_est}</div>
        <div class="kv-grid">${kv([
          ["Premium", o.premium ? fmt(o.premium) : "enter below"], ["Premium SL", o.premium_stop ? fmt(o.premium_stop) : "-"],
          ["Prem T1", o.premium_targets ? fmt(o.premium_targets[0]) : "-"], ["Prem T2", o.premium_targets ? fmt(o.premium_targets[1]) : "-"],
          ["Risk budget", "&#8377;" + fmt(o.risk_budget, 0)], ["Suggested lots", o.lots ?? "-"],
        ])}</div>
        ${(o.size_notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join("")}
      </div>
      <div class="plan-block">
        <div class="muted" style="font-size:12px">Position calculator &amp; paper trade</div>
        <div class="form-row">
          <label>Premium<input id="calcPrem" type="number" step="0.05" value="${o.premium ?? ""}"></label>
          <label>Lot size<input id="calcLot" type="number" value="${o.lot_size ?? ""}"></label>
          <label>Lots<input id="calcLots" type="number" min="1" value="${Math.max(1, o.lots || 1)}"></label>
        </div>
        <div id="calcOut" class="muted" style="font-size:12px;margin-top:6px"></div>
        <div class="form-row"><button class="primary" id="paperBtn">Paper trade this</button></div>
      </div>`;
    const calc = () => {
      const prem = parseFloat($("calcPrem").value), lot = parseInt($("calcLot").value, 10);
      if (!(prem > 0 && lot > 0)) { $("calcOut").textContent = "Enter premium and lot size to size the trade."; return; }
      const perUnit = Math.min(prem * state.config.premium_stop_pct / 100, p.risk_points * o.delta_est);
      const lots = Math.floor(o.risk_budget / (perUnit * lot));
      $("calcOut").innerHTML = `Risk/unit &#8377;${fmt(perUnit)} · 1 lot risks &#8377;${fmt(perUnit * lot, 0)} · max lots for budget: <b>${lots}</b> · premium SL &#8377;${fmt(prem - perUnit)}`;
    };
    ["calcPrem", "calcLot"].forEach((id) => $(id).addEventListener("input", calc));
    calc();
    $("paperBtn").onclick = async () => {
      const body = {
        symbol: d.symbol, direction: p.direction, contract: o.contract,
        reason: d.checks.filter((c) => c[p.direction === "CALL" ? "bull" : "bear"]).map((c) => c.key).join(", ") + ` (${d.status})`,
        entry_underlying: p.entry, stop_underlying: p.stop, target_underlying: p.targets[0],
        entry_premium: parseFloat($("calcPrem").value) || null,
        lots: parseInt($("calcLots").value, 10) || 1, lot_size: parseInt($("calcLot").value, 10) || 1,
      };
      await api("/api/trades", { method: "POST", body: JSON.stringify(body) });
      loadTrades();
    };
  }

  function renderOptions(d) {
    const c = d.chain;
    $("chainSrc").textContent = c ? `(${c.source}, expiry ${c.expiry})` : "";
    if (!c) {
      $("chainKv").innerHTML = '<span class="muted">Option chain unavailable (NSE may be blocking requests, or this symbol has no F&amp;O).</span>';
      $("chainBody").innerHTML = "";
    } else {
      const pcrNote = c.pcr > 1.2 ? " bullish" : c.pcr < 0.7 ? " bearish" : " neutral";
      $("chainKv").innerHTML = kv([
        ["PCR (OI)", `${fmt(c.pcr)}<span class="muted">${pcrNote}</span>`], ["Max pain", fmt(c.max_pain, 0)],
        ["Resistance (max CE OI)", fmt(c.resistance, 0)], ["Support (max PE OI)", fmt(c.support, 0)],
        ["ATM", fmt(c.atm, 0)], ["Spot", fmt(c.spot)],
      ]);
      const big = (n) => n >= 1e5 ? (n / 1e5).toFixed(1) + "L" : fmt(n, 0);
      $("chainBody").innerHTML = c.chain.map((r) => `<tr class="${r.strike === c.atm ? "atm" : ""} ${r.strike === c.resistance ? "res" : ""} ${r.strike === c.support ? "sup" : ""}">
        <td class="num">${big(r.ce_oi)}</td><td class="num">${fmt(r.ce_ltp)}</td><td class="c">${fmt(r.strike, r.strike % 1 ? 1 : 0)}</td>
        <td class="num">${fmt(r.pe_ltp)}</td><td class="num">${big(r.pe_oi)}</td></tr>`).join("");
    }
    const lv = d.levels;
    $("cprWidth").textContent = `width ${fmt(lv.width_pct)}%${d.cpr_narrow ? " · narrow: trending day likely" : ""}`;
    $("levelsKv").innerHTML = kv(["R3", "S3", "R2", "S2", "R1", "S1", "TC", "BC", "P"].map((k) => [k, fmt(lv[k])]));
  }

  async function renderFund(sym) {
    const card = $("fundCard");
    card.innerHTML = '<span class="muted">Loading fundamentals...</span>';
    const f = await api(`/api/fundamentals/${encodeURIComponent(sym)}`).catch((e) => ({ available: false, message: e.message }));
    if (state.selected !== sym) return;
    if (!f.available) { card.innerHTML = `<h2>Fundamentals</h2><span class="muted">${esc(f.message)}</span>`; return; }
    card.innerHTML = `<h2>Fundamentals: ${esc(f.name || sym)}</h2>
      <div class="muted" style="font-size:12px;margin-bottom:8px">${esc(f.sector || "")} · ${esc(f.industry || "")}</div>
      <div class="kv-grid">${kv([
        ["Verdict", `${esc(f.verdict)} (${f.score}/${f.out_of})`], ["Mkt cap (Cr)", fmt(f.market_cap_cr, 0)],
        ["P/B", fmt(f.pb)], ["Div yield", f.dividend_yield != null ? fmt(f.dividend_yield) + "%" : "-"],
        ["52w high", fmt(f.high_52w)], ["52w low", fmt(f.low_52w)],
      ])}</div>
      <table class="fund-checks"><tbody>${f.checks.map((c) => `<tr><td>${esc(c.label)}</td><td>${esc(c.value)} ${c.pass === null ? "" : tick(c.pass)}</td></tr>`).join("")}</tbody></table>
      ${f.events.map((e) => `<div class="note">${esc(e)}</div>`).join("")}
      <div class="muted" style="font-size:11px;margin-top:8px">Fundamentals are context for stock options, not an intraday trigger.</div>`;
  }

  // ---------- alerts ----------
  function beep() {
    if (!state.sound) return;
    try {
      const ctx = new AudioContext(); const o = ctx.createOscillator(); const g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination); o.frequency.value = 880; g.gain.value = 0.08;
      o.start(); o.stop(ctx.currentTime + 0.25);
    } catch (_) { /* audio blocked until user interacts */ }
  }

  function addAlert(a, live) {
    const ul = $("alerts");
    ul.querySelector(".empty")?.remove();
    const li = document.createElement("li");
    li.className = a.level;
    li.innerHTML = `<div class="t">${esc(a.time)} · ${esc(a.title)}</div><div class="m">${esc(a.message)}</div>`;
    li.style.cursor = "pointer";
    li.onclick = () => select(a.symbol);
    ul.prepend(li);
    if (live && (a.level === "buy" || a.level === "exit")) {
      beep();
      if (state.notify && Notification.permission === "granted") new Notification(a.title, { body: a.message });
    }
  }

  // ---------- data ----------
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }

  async function loadAnalysis() {
    if (!state.selected || state.loading) return;
    state.loading = true;
    const sym = state.selected;
    try {
      const d = await api(`/api/analysis/${encodeURIComponent(sym)}`);
      if (sym !== state.selected) return;
      state.data = d;
      renderSignal(d); drawCharts(d); renderOptions(d);
      if (d.vix) $("vixVal").textContent = fmt(d.vix);
      $("clock").textContent = d.now.slice(11, 16);
    } catch (e) {
      $("statusMsg").textContent = `Could not load ${sym}: ${e.message}`;
    } finally { state.loading = false; }
  }

  function select(sym) {
    state.selected = sym;
    renderScan();
    loadAnalysis();
    if (!$("tab-fund").classList.contains("hidden")) renderFund(sym);
  }

  async function loadTrades() {
    const { trades, stats } = await api("/api/trades");
    $("stats").innerHTML = `<span>Trades <b>${stats.trades}/${stats.target_sample}</b></span><span>Win rate <b>${stats.win_rate}%</b></span>
      <span>Avg win <b class="up">${fmt(stats.avg_win, 0)}</b></span><span>Avg loss <b class="down">${fmt(stats.avg_loss, 0)}</b></span>
      <span>Expectancy <b>${fmt(stats.expectancy, 0)}</b></span><span>Net <b class="${stats.net_pnl >= 0 ? "up" : "down"}">${fmt(stats.net_pnl, 0)}</b></span>`;
    $("tradeBody").innerHTML = trades.length ? trades.map((t) => `<tr>
      <td>${t.id}</td><td>${esc(t.entry_time.slice(5, 16).replace("T", " "))}</td><td>${esc(t.contract || t.symbol)}</td>
      <td class="${t.direction === "CALL" ? "up" : "down"}">${t.direction}</td>
      <td class="num">${fmt(t.entry_underlying)}</td><td class="num">${fmt(t.stop_underlying)}</td><td class="num">${fmt(t.target_underlying)}</td>
      <td class="num">${fmt(t.entry_premium)}</td><td class="num">${t.lots}x${t.lot_size}</td>
      <td class="num">${t.exit_time ? fmt(t.exit_premium ?? t.exit_underlying) : "open"}</td>
      <td class="num ${t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : ""}">${t.pnl == null ? "-" : fmt(t.pnl, 0)}</td>
      <td title="${esc(t.reason)}">${esc((t.exit_reason || t.reason || "").slice(0, 40))}</td>
      <td>${t.exit_time ? `<button class="link" data-del="${t.id}">delete</button>` : `<button data-close="${t.id}">Close</button>`}</td></tr>`).join("")
      : '<tr><td colspan="13" class="muted">No paper trades yet. Use "Paper trade this" on a signal; aim for 20-30 logged setups before using real capital.</td></tr>';
  }

  $("tradeBody").addEventListener("click", async (e) => {
    const close = e.target.dataset.close, del = e.target.dataset.del;
    if (close) {
      const prem = prompt("Exit premium (leave blank to use the underlying's current price):");
      if (prem === null) return;
      const reason = prompt("Exit reason (stop, target, supertrend flip, 2:30 exit...):", "") || "";
      const t = (await api("/api/trades")).trades.find((x) => String(x.id) === close);
      const ltp = state.rows.find((r) => r.symbol === t?.symbol)?.ltp ?? null;
      await api(`/api/trades/${close}/close`, { method: "POST", body: JSON.stringify({
        exit_premium: prem.trim() ? parseFloat(prem) : null, exit_underlying: prem.trim() ? null : ltp, reason }) });
      loadTrades();
    } else if (del && confirm("Delete this trade from the journal?")) {
      await api(`/api/trades/${del}`, { method: "DELETE" });
      loadTrades();
    }
  });

  // ---------- wiring ----------
  $("scanBody").addEventListener("click", async (e) => {
    const rm = e.target.dataset.rm;
    if (rm) {
      e.stopPropagation();
      const symbols = state.config.watchlist.filter((s) => s !== rm);
      if (!symbols.length) return;
      state.config.watchlist = (await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbols }) })).watchlist;
      state.rows = state.rows.filter((r) => r.symbol !== rm);
      renderScan();
      return;
    }
    const tr = e.target.closest("tr");
    if (tr) select(tr.dataset.sym);
  });

  $("addForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sym = $("addInput").value.trim().toUpperCase().replace(/\.NS$/, "");
    if (!sym) return;
    const symbols = [...new Set([...state.config.watchlist, sym])];
    state.config.watchlist = (await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbols }) })).watchlist;
    state.rows.push({ symbol: sym, status: "ERROR", message: "Waiting for next scan" });
    $("addInput").value = "";
    select(sym);
  });

  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + b.dataset.tab));
    if (b.dataset.tab === "fund" && state.selected) renderFund(state.selected);
  }));

  $("notifyBtn").onclick = async () => {
    if (!("Notification" in window)) return alert("This browser does not support notifications.");
    state.notify = (await Notification.requestPermission()) === "granted";
    $("notifyBtn").textContent = state.notify ? "Alerts on" : "Alerts blocked";
  };
  $("soundBtn").onclick = () => { state.sound = !state.sound; $("soundBtn").textContent = state.sound ? "Sound on" : "Sound off"; };
  $("clearAlerts").onclick = () => { $("alerts").innerHTML = '<li class="empty">No alerts.</li>'; };

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "scan") {
        state.rows = msg.rows;
        renderScan();
        $("vixVal").textContent = fmt(msg.vix);
        $("lastScan").textContent = msg.last_scan || "-";
        loadAnalysis();
        loadTrades();
      } else if (msg.type === "alert") {
        addAlert(msg.alert, true);
      }
    };
    ws.onclose = () => setTimeout(connect, 3000);
  }

  async function init() {
    state.config = await api("/api/config");
    $("providerChip").textContent = `Data: ${state.config.provider}` + (state.config.provider === "demo" ? " (simulated)" : "");
    const past = await api("/api/alerts");
    past.reverse().forEach((a) => addAlert(a, false));
    state.selected = state.config.watchlist[0];
    connect();
    loadAnalysis();
    loadTrades();
  }
  init();
})();
