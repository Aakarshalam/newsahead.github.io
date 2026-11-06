// NewsAlpha front end. Reads the JSON produced by the Python pipeline in data/.
'use strict';

const state = {
  signals: [], sigMeta: null, backtest: null, pipeline: null, taxonomy: null, history: null,
  dirChart: null, catChart: null, loadedAt: null, compiled: null,
};

// ─── helpers ─────────────────────────────────────────────────────────────────
const el = id => document.getElementById(id);
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ESC[c]);
const safeUrl = u => /^https?:\/\//i.test(u || '') ? esc(u) : '#';
const fmt = (n, d = 1) => (n === null || n === undefined || Number.isNaN(n)) ? '—' : Number(n).toFixed(d);
const sgn = (n, d = 1) => n === null || n === undefined ? '—' : (n > 0 ? '+' : '') + Number(n).toFixed(d);
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function ago(iso) {
  if (!iso) return '—';
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

function dirPill(d, kind) {
  const cls = {bull: 'pill-bull', bear: 'pill-bear'}[d] || 'pill-mix';
  const lbl = {bull: '↑ Up', bear: '↓ Down'}[d] || '~ No clear direction';
  return `<span class="pill ${cls}" title="${kind === 'fx' ? 'For USD/xxx pairs, up = USD stronger' : 'Historical direction of the average price move'}">${lbl}</span>`;
}
const scoreCls = s => s >= 80 ? 'score-hi' : s >= 60 ? 'score-md' : 'score-lo';
const confCls = c => c === 'High' ? 'conf-h' : c === 'Med' ? 'conf-m' : 'conf-l';
const moveCls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';

function barHtml(pct, color) {
  if (pct === null || pct === undefined) return '—';
  return `<div class="bar-wrap"><div class="bar-bg"><div class="bar-fill" style="width:${Math.min(pct, 100)}%;background:${color}"></div></div><span class="bar-num">${Math.round(pct)}%</span></div>`;
}
const dirColor = d => d === 'bull' ? css('--green') : d === 'bear' ? css('--red') : css('--amber');

function fillSelect(id, values) {
  const s = el(id);
  const keep = s.value;
  [...s.querySelectorAll('option:not(:first-child)')].forEach(o => o.remove());
  values.forEach(v => { const o = document.createElement('option'); o.value = o.textContent = v; s.appendChild(o); });
  s.value = keep;
}

function notice(html, kind = 'warn') {
  const n = el('global-notice');
  n.className = 'notice ' + kind;
  n.innerHTML = html;
  n.hidden = !html;
}

// ─── data loading ────────────────────────────────────────────────────────────
async function getJSON(path) {
  if (window.__NA_FIXTURE && path in window.__NA_FIXTURE) return window.__NA_FIXTURE[path];  // UI tests
  const r = await fetch(`${path}?t=${Date.now()}`, {cache: 'no-store'});
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function loadAll() {
  const res = await Promise.allSettled([
    getJSON('data/signals.json'), getJSON('data/backtest.json'), getJSON('data/pipeline.json'),
    getJSON('data/taxonomy.json'), getJSON('data/signal_history.json'),
  ]);
  const [sig, bt, pipe, tax, hist] = res.map(r => r.status === 'fulfilled' ? r.value : null);
  const failed = res.map((r, i) => r.status === 'rejected' ? ['signals', 'backtest', 'pipeline', 'taxonomy', 'history'][i] : null).filter(Boolean);
  state.signals = sig?.signals || [];
  state.sigMeta = sig;
  state.backtest = bt;
  state.pipeline = pipe;
  state.taxonomy = tax;
  state.history = hist;
  state.compiled = null;
  state.loadedAt = Date.now();
  if (failed.length) {
    notice(`Could not load ${failed.map(esc).join(', ')} data. If you are opening <code>index.html</code> directly from disk, serve the folder instead (e.g. <code>python3 -m http.server</code>) or run the pipeline first (<code>python3 -m pipeline.run all</code>).`);
  } else {
    const age = (Date.now() - new Date(pipe.updated).getTime()) / 3600e3;
    notice(age > 3 ? `Pipeline output is ${Math.round(age)}h old. The scheduled GitHub Action may be paused or failing; check the Pipeline page.` : '');
  }
  updateNav();
  populateFilters();
  renderAll();
}

function updateNav() {
  const upd = state.pipeline?.updated || state.sigMeta?.updated;
  const stale = !upd || (Date.now() - new Date(upd)) > 3 * 3600e3;
  el('nav-dot').classList.toggle('stale', stale);
  el('nav-status-txt').textContent = upd ? `updated ${ago(upd)}` : 'no data';
}

function populateFilters() {
  const rows = state.backtest?.rows || [];
  const mkts = [...new Set(rows.map(r => r.mkt))];
  const cats = [...new Set(rows.map(r => r.cat))];
  fillSelect('dash-mkt-filter', [...new Set(state.signals.map(s => s.mkt))].sort());
  fillSelect('bt-mkt', mkts.sort());
  fillSelect('bt-cat', cats.sort());
  fillSelect('im-cat', cats.sort());
}

function renderAll() {
  renderDashboard();
  const active = document.querySelector('.page.active')?.id?.replace('page-', '');
  renderPage(active);
}

function renderPage(pg) {
  if (pg === 'backtest') renderBacktest();
  if (pg === 'impact') renderImpact();
  if (pg === 'pipeline') renderPipeline();
  if (pg === 'method') renderMethod();
}

// ─── navigation ──────────────────────────────────────────────────────────────
function showPage(pg) {
  if (!el('page-' + pg)) pg = 'dashboard';
  document.querySelectorAll('.nav-link').forEach(l => l.classList.toggle('active', l.dataset.page === pg));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + pg));
  renderPage(pg);
}
document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', e => {
  e.preventDefault();
  history.replaceState(null, '', '#' + link.dataset.page);
  showPage(link.dataset.page);
}));

// ─── DASHBOARD ───────────────────────────────────────────────────────────────
function renderDashboard() {
  const sigs = state.signals;
  const day = sigs.filter(s => Date.now() - new Date(s.published) < 86400e3);
  const hist = (state.history?.signals || []).filter(h => h.realized && h.realized.correct !== null && h.realized.sessions >= 1);
  const correct = hist.filter(h => h.realized.correct).length;
  const articles = state.pipeline?.totals?.articles_24h;
  el('dash-metrics').innerHTML = `
    <div class="mcard"><div class="lbl">Articles scanned (24h)</div><div class="val">${articles?.toLocaleString() ?? '—'}</div><div class="sub">${state.pipeline?.health?.filter(h => h.status === 'ok').length ?? 0} feeds healthy</div></div>
    <div class="mcard"><div class="lbl">Event signals (24h)</div><div class="val green">${day.length}</div><div class="sub">${sigs.length} in the last 14 days</div></div>
    <div class="mcard"><div class="lbl">High-score signals (24h)</div><div class="val ${day.some(s => s.score >= 80) ? 'red' : ''}">${day.filter(s => s.score >= 70).length}</div><div class="sub">score ≥ 70</div></div>
    <div class="mcard"><div class="lbl">Live direction hit rate</div><div class="val">${hist.length ? Math.round(100 * correct / hist.length) + '%' : '—'}</div><div class="sub">${hist.length ? `${correct}/${hist.length} directional signals so far` : 'needs ≥1 session after a signal'}</div></div>`;
  el('feed-updated').innerHTML = state.sigMeta ? `<span class="pulse-dot sm"></span>run ${esc(ago(state.sigMeta.updated))}` : '';
  renderFeed();
  renderCharts();
  renderTrackRecord();
}

function renderFeed() {
  const mktF = el('dash-mkt-filter').value;
  const scoreF = +el('dash-score-filter').value || 0;
  const items = state.signals.filter(s => (!mktF || s.mkt === mktF) && s.score >= scoreF);
  const feed = el('signal-feed');
  if (!items.length) {
    feed.innerHTML = `<div class="empty">${state.signals.length ? 'No signals match these filters.' : 'No event signals yet. The pipeline only creates a signal when a headline matches a known event type.'}</div>`;
    return;
  }
  feed.innerHTML = items.slice(0, 60).map(s => {
    const r = s.realized;
    const real = r ? `<div class="si-real ${moveCls(r.signed ?? r.ret)}" title="Basket move since the reference close${s.benchmark ? ' (vs ' + esc(s.benchmark) + ')' : ''}">${sgn(r.ret, 2)}% · ${r.sessions}d</div>` : '<div class="si-real">pending</div>';
    return `
    <button class="signal-item" data-id="${esc(s.id)}">
      <div>
        <div class="si-time">${esc(ago(s.published))}</div>
        <div class="si-src">${esc(s.publisher)}</div>
      </div>
      <div>
        <div class="si-title">${esc(s.title)}</div>
        <div class="si-tags">
          <span class="tag tag-cat">${esc(s.event)}</span>
          <span class="tag">${esc(s.mkt)}</span>
          ${dirPill(s.dir)}
          ${s.coverage > 1 ? `<span class="tag" title="${esc(s.also_reported_by.join(', '))}">${s.coverage} articles</span>` : ''}
          ${s.companies.slice(0, 3).map(c => `<span class="tag tag-co">${esc(c)}</span>`).join('')}
          ${s.method !== 'rules' ? `<span class="tag tag-ai">${esc(s.method)}</span>` : ''}
        </div>
      </div>
      <div class="si-right">
        <div class="score-num ${scoreCls(s.score)}">${s.score}</div>
        <div class="score-win">${s.window ? 'best ' + esc(s.window) : '—'}</div>
        ${real}
      </div>
    </button>`;
  }).join('');
}
el('signal-feed').addEventListener('click', e => {
  const b = e.target.closest('.signal-item');
  if (b) openDrawer(b.dataset.id);
});

function renderCharts() {
  if (typeof Chart === 'undefined') return;
  const sigs = state.signals;
  const counts = ['bull', 'bear', 'mix'].map(d => sigs.filter(s => s.dir === d).length);
  const colors = [css('--green'), css('--red'), css('--amber')];
  state.dirChart?.destroy();
  state.dirChart = new Chart(el('dir-chart'), {
    type: 'doughnut',
    data: {labels: ['Up', 'Down', 'No clear direction'], datasets: [{data: counts, backgroundColor: colors, borderWidth: 0}]},
    options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, cutout: '70%'},
  });
  el('dir-legend').innerHTML = ['Up', 'Down', 'No clear direction'].map((l, i) =>
    `<span><i style="background:${colors[i]}"></i>${l} <b>${counts[i]}</b></span>`).join('');
  const cats = {};
  sigs.forEach(s => { cats[s.cat] = (cats[s.cat] || 0) + 1; });
  state.catChart?.destroy();
  state.catChart = new Chart(el('cat-chart'), {
    type: 'bar',
    data: {labels: Object.keys(cats), datasets: [{data: Object.values(cats), backgroundColor: css('--accent'), borderRadius: 4}]},
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: 'y', plugins: {legend: {display: false}},
      scales: {x: {grid: {color: 'rgba(127,127,127,.12)'}, ticks: {color: css('--text3'), precision: 0}},
               y: {grid: {display: false}, ticks: {color: css('--text2')}}},
    },
  });
}

function renderTrackRecord() {
  const rows = (state.history?.signals || []).filter(h => h.realized && h.realized.signed !== null && h.realized.sessions >= 1);
  if (!rows.length) {
    el('track-record').innerHTML = '<div class="empty">No signals have a full trading session behind them yet. Results appear here once prices move after a signal.</div>';
    return;
  }
  const byType = {};
  rows.forEach(r => { (byType[r.event] ||= []).push(r.realized.signed); });
  el('track-record').innerHTML = `<table class="mini-table"><thead><tr><th>Event type</th><th>n</th><th>Hit</th><th>Avg signed move</th></tr></thead><tbody>${
    Object.entries(byType).sort((a, b) => b[1].length - a[1].length).map(([k, v]) => {
      const hit = v.filter(x => x > 0).length / v.length * 100;
      const avg = v.reduce((a, b) => a + b, 0) / v.length;
      return `<tr><td>${esc(k)}</td><td>${v.length}</td><td>${Math.round(hit)}%</td><td class="${moveCls(avg)}">${sgn(avg, 2)}%</td></tr>`;
    }).join('')}</tbody></table>
    <div class="fine">Signed move = basket return in the backtested direction, measured from the last close before the signal. Small samples; not a performance claim.</div>`;
}

function winBoxes(windows, opt) {
  const wl = state.backtest?.method?.windows || ['1d', '3d', '1w', '2w', '1m'];
  return `<div class="win-row">${wl.map(w => {
    const s = windows?.[w];
    return `<div class="win-box${w === opt ? ' best' : ''}" title="${s ? `n=${s.n}, median ${s.median}%, hit ${s.hit}%, t=${s.t}, p=${s.p}` : 'insufficient data'}">
      <div class="wl">${w}</div><div class="wv ${moveCls(s?.mean)}">${s ? sgn(s.mean, 2) + '%' : '—'}</div><div class="wt">${s ? 't ' + fmt(s.t, 1) : ''}</div></div>`;
  }).join('')}</div>`;
}

function openDrawer(id) {
  const s = state.signals.find(x => x.id === id);
  if (!s) return;
  el('drawer-title').textContent = s.event;
  const bd = s.score_breakdown;
  const weights = state.taxonomy?.score_weights || [];
  const rows = (state.backtest?.rows || []).filter(r => r.type === s.type);
  const prim = rows.find(r => r.mkt === s.mkt) || rows[0];
  el('drawer-body').innerHTML = `
    <div class="drawer-body-grid">
      <div>
        <div class="d-section-title">Headline</div>
        <a class="d-headline" href="${safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>
        <div class="d-row"><span class="d-key">Source</span><span class="d-val">${esc(s.publisher)}${s.also_reported_by.length ? ` <span class="muted">+ ${s.also_reported_by.length} more</span>` : ''}</span></div>
        <div class="d-row"><span class="d-key">Published</span><span class="d-val">${esc(new Date(s.published).toLocaleString())} <span class="muted">(${esc(s.timing)} US session)</span></span></div>
        <div class="d-row"><span class="d-key">Category</span><span class="d-val">${esc(s.cat)} · ${esc(s.geo)}</span></div>
        <div class="d-row"><span class="d-key">Classified by</span><span class="d-val">${esc(s.method)}${s.rule_hits?.length ? ` <span class="muted">(${s.rule_hits.map(esc).join(', ')})</span>` : ''}</span></div>
        <div class="d-section-title" style="margin-top:14px">Tracked since signal</div>
        ${s.tracking?.length ? `<table class="mini-table"><thead><tr><th>Instrument</th><th>Ref close</th><th>Last</th><th>Move</th></tr></thead><tbody>${
          s.tracking.map(t => t.status === 'live'
            ? `<tr><td>${esc(t.symbol)}</td><td>${fmt(t.ref, 2)} <span class="muted">${esc(t.ref_date)}</span></td><td>${fmt(t.last, 2)}</td><td class="${moveCls(t.ret)}">${sgn(t.ret, 2)}%</td></tr>`
            : `<tr><td>${esc(t.symbol)}</td><td colspan="3" class="muted">waiting for first reaction session</td></tr>`).join('')}</tbody></table>
          ${s.benchmark ? `<div class="fine">Moves are relative to ${esc(s.benchmark)}.</div>` : ''}` : '<div class="muted">No instruments tracked.</div>'}
      </div>
      <div>
        <div class="d-section-title">Score ${s.score} / 100</div>
        ${weights.map(w => `
          <div class="score-bar-row">
            <span class="score-bar-label" title="${esc(w.desc)}">${esc(w.name.replace(/_/g, ' '))} <span class="muted">(${w.pct}%)</span></span>
            <div class="score-bar-bg"><div class="score-bar-fill" style="width:${bd[w.name] ?? 0}%"></div></div>
            <span class="score-bar-val">${bd[w.name] ?? '—'}</span>
          </div>`).join('')}
        <div class="d-section-title" style="margin-top:14px">All backtested markets for this event</div>
        <table class="mini-table"><thead><tr><th>Market</th><th>Dir</th><th>Best</th><th>Avg</th><th>Conf</th></tr></thead><tbody>${
          s.impacts.map(i => `<tr><td>${esc(i.mkt)}<div class="muted">${esc(i.name)}</div></td><td>${dirPill(i.dir)}</td><td>${esc(i.optW || '—')}</td><td class="${moveCls(i.move)}">${sgn(i.move, 2)}%</td><td class="${confCls(i.conf)}">${esc(i.conf)}</td></tr>`).join('')}</tbody></table>
      </div>
      <div>
        <div class="d-section-title">Historical reaction: ${esc(prim?.mkt || '')}</div>
        ${prim ? winBoxes(prim.windows, prim.optW) : '<div class="muted">No backtest for this type.</div>'}
        ${prim ? `<div class="fine">${prim.samples} past events · ${esc(prim.adjustment)} · hit rate ${fmt(prim.cons, 0)}% · p(adj) ${fmt(prim.p_adj, 3)}</div>` : ''}
        ${prim?.note ? `<div class="fine warn">${esc(prim.note)}</div>` : ''}
        <div class="d-section-title" style="margin-top:14px">Most reactive instruments</div>
        <div>${(prim?.instruments || []).slice(0, 8).map(i => `<span class="co-chip" title="n=${i.n}, hit ${i.hit}%, t=${i.t}">${esc(i.name || i.symbol)} <b class="${moveCls(i.mean)}">${sgn(i.mean, 1)}%</b></span>`).join('') || '<span class="muted">—</span>'}</div>
      </div>
    </div>`;
  el('signal-drawer').classList.add('open');
  el('drawer-close').focus();
}
function closeDrawer() { el('signal-drawer').classList.remove('open'); }
el('drawer-close').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

el('dash-mkt-filter').addEventListener('change', renderFeed);
el('dash-score-filter').addEventListener('change', renderFeed);
el('refresh-btn').addEventListener('click', loadAll);

// ─── BACKTEST ────────────────────────────────────────────────────────────────
function btRows() {
  const cat = el('bt-cat').value, mkt = el('bt-mkt').value, conf = el('bt-conf').value;
  const q = el('bt-search').value.trim().toLowerCase();
  return (state.backtest?.rows || []).filter(r => {
    if (cat && r.cat !== cat) return false;
    if (mkt && r.mkt !== mkt) return false;
    if (conf === 'High' && r.conf !== 'High') return false;
    if (conf === 'Med' && r.conf === 'Low') return false;
    if (q && !`${r.event} ${r.name} ${r.geo} ${r.cat}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderBacktest() {
  const bt = state.backtest;
  if (!bt) { el('bt-tbody').innerHTML = `<tr><td colspan="10" class="empty">${state.loadedAt ? 'Backtest data could not be loaded.' : 'Loading…'}</td></tr>`; return; }
  const rows = btRows();
  const nEvents = Object.values(bt.event_counts || {}).reduce((a, b) => a + b, 0);
  const sig = rows.filter(r => r.conf !== 'Low').length;
  el('bt-metrics').innerHTML = `
    <div class="mcard"><div class="lbl">Historical events</div><div class="val">${nEvents}</div><div class="sub">${Object.keys(bt.event_counts).length} event types</div></div>
    <div class="mcard"><div class="lbl">Market reactions tested</div><div class="val">${rows.length}</div><div class="sub">event type × market basket (filtered)</div></div>
    <div class="mcard"><div class="lbl">Statistically reliable</div><div class="val green">${sig}</div><div class="sub">Med/High confidence after Bonferroni</div></div>
    <div class="mcard"><div class="lbl">Backtest built</div><div class="val small">${esc(ago(bt.generated))}</div><div class="sub">${esc(new Date(bt.generated).toLocaleDateString())}</div></div>`;
  if (!rows.length) { el('bt-tbody').innerHTML = '<tr><td colspan="10" class="empty">No results match these filters.</td></tr>'; return; }
  el('bt-tbody').innerHTML = rows.map((r, i) => `
    <tr class="bt-row" data-i="${i}" tabindex="0">
      <td class="bt-expander">▶</td>
      <td><div class="strong">${esc(r.event)}</div><div class="muted">${esc(r.cat)} · ${esc(r.geo)}${r.outcome_selected ? ' · <span class="warn" title="' + esc(r.note) + '">selection-biased</span>' : ''}</div></td>
      <td><div>${esc(r.mkt)}</div><div class="muted">${esc(r.name)}</div></td>
      <td>${dirPill(r.dir, r.mkt === 'Forex' ? 'fx' : '')}</td>
      <td>${barHtml(r.cons, dirColor(r.dir))}</td>
      <td class="num ${moveCls(r.move)}">${sgn(r.move, 2)}%</td>
      <td class="num accent">${esc(r.optW || '—')}</td>
      <td class="num">${fmt(r.t, 2)}</td>
      <td class="num muted">${r.samples}</td>
      <td class="${confCls(r.conf)}">${esc(r.conf)}</td>
    </tr>
    <tr class="detail-row" id="bt-detail-${i}" hidden><td colspan="10"></td></tr>`).join('');
  state._btRows = rows;
}

function btDetail(r) {
  const wl = state.backtest.method.windows;
  return `<div class="detail-expand">
    <div>
      <div class="d-section-title">By window (avg ${esc(r.adjustment)} move)</div>
      ${winBoxes(r.windows, r.optW)}
      <div class="fine">Hover a window for n, median, hit rate and p-value. Best window = largest |t|. Adjusted p = ${fmt(r.p_adj, 4)}.</div>
      ${r.persistence !== null && r.persistence !== undefined ? `<div class="fine">1-month move is ${fmt(r.persistence * 100, 0)}% of the best-window move (${r.persistence > 0.7 ? 'persists' : r.persistence < 0.3 ? 'fades' : 'partly fades'}).</div>` : ''}
      ${r.note ? `<div class="fine warn">${esc(r.note)}</div>` : ''}
      <div class="d-section-title" style="margin-top:12px">Instruments at ${esc(r.optW || '—')}</div>
      <table class="mini-table"><thead><tr><th>Instrument</th><th>n</th><th>Avg</th><th>Hit</th><th>t</th></tr></thead><tbody>${
        r.instruments.map(i => `<tr><td>${esc(i.name || i.symbol)} <span class="muted">${i.name && i.name !== i.symbol ? esc(i.symbol) : ''}</span></td><td>${i.n}</td><td class="${moveCls(i.mean)}">${sgn(i.mean, 2)}%</td><td>${fmt(i.hit, 0)}%</td><td>${fmt(i.t, 1)}</td></tr>`).join('')}</tbody></table>
    </div>
    <div>
      <div class="d-section-title">Every event in this sample (${r.events.length})</div>
      <div class="event-list"><table class="mini-table"><thead><tr><th>Date</th><th>Event</th>${wl.map(w => `<th>${w}</th>`).join('')}</tr></thead><tbody>${
        r.events.map(e => `<tr><td class="nowrap">${esc(e.date)}</td><td><a href="${safeUrl(e.source)}" target="_blank" rel="noopener noreferrer">${esc(e.headline)}</a>${r.source !== 'fred' && e.tickers?.length && r.mkt === 'Single Stocks' ? ` <span class="muted">${e.tickers.map(esc).join(', ')}</span>` : ''}</td>${
          wl.map(w => `<td class="num ${moveCls(e.returns[w])}">${e.returns[w] !== undefined ? sgn(e.returns[w], 1) : '—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    </div>
  </div>`;
}

el('bt-tbody').addEventListener('click', e => toggleBt(e.target.closest('.bt-row')));
el('bt-tbody').addEventListener('keydown', e => { if (e.key === 'Enter') toggleBt(e.target.closest('.bt-row')); });
function toggleBt(tr) {
  if (!tr) return;
  const i = tr.dataset.i, det = el('bt-detail-' + i);
  const open = !det.hidden;
  document.querySelectorAll('#bt-tbody .detail-row').forEach(d => { d.hidden = true; });
  document.querySelectorAll('#bt-tbody .bt-expander').forEach(a => { a.textContent = '▶'; });
  if (!open) {
    det.firstElementChild.innerHTML = btDetail(state._btRows[i]);
    det.hidden = false;
    tr.querySelector('.bt-expander').textContent = '▼';
  }
}
['bt-cat', 'bt-mkt', 'bt-conf'].forEach(id => el(id).addEventListener('change', renderBacktest));
el('bt-search').addEventListener('input', renderBacktest);

// ─── IMPACT MAP ──────────────────────────────────────────────────────────────
function renderImpact() {
  const cat = el('im-cat').value, conf = el('im-conf').value;
  const byType = {};
  (state.backtest?.rows || []).forEach(r => {
    if (cat && r.cat !== cat) return;
    if (conf === 'Med' && r.conf === 'Low') return;
    (byType[r.type] ||= []).push(r);
  });
  const types = Object.values(byType);
  if (!types.length) { el('impact-cards').innerHTML = '<div class="empty">Nothing matches these filters.</div>'; return; }
  el('impact-cards').innerHTML = types.map(rows => {
    const best = [...rows].sort((a, b) => Math.abs(b.t || 0) - Math.abs(a.t || 0))[0];
    return `<details class="impact-card">
      <summary class="impact-header">
        <div>
          <div class="impact-event">${esc(best.event)}</div>
          <div class="impact-meta">
            <span class="tag tag-cat">${esc(best.cat)}</span><span class="tag tag-geo">${esc(best.geo)}</span>
            <span class="tag">${Math.max(...rows.map(r => r.samples))} events</span>
            ${best.outcome_selected ? '<span class="tag warn">selection-biased</span>' : ''}
          </div>
        </div>
        <div class="impact-score">
          <div class="is-num ${moveCls(best.move)}">${sgn(best.move, 2)}%</div>
          <div class="is-lbl">${esc(best.mkt)} @ ${esc(best.optW || '—')} · <span class="${confCls(best.conf)}">${esc(best.conf)}</span></div>
        </div>
      </summary>
      <div class="impact-grid">${rows.map(r => `
        <div>
          <div class="ig-title">${esc(r.mkt)}: ${esc(r.name)} ${dirPill(r.dir, r.mkt === 'Forex' ? 'fx' : '')}</div>
          ${winBoxes(r.windows, r.optW)}
          <div style="margin-top:8px">${r.instruments.slice(0, 6).map(i => `<span class="co-chip" title="n=${i.n} hit ${i.hit}%">${esc(i.name || i.symbol)} <b class="${moveCls(i.mean)}">${sgn(i.mean, 1)}%</b></span>`).join('')}</div>
          <div class="fine">Confidence <span class="${confCls(r.conf)}">${esc(r.conf)}</span> · hit ${fmt(r.cons, 0)}% · t ${fmt(r.t, 2)} · ${esc(r.adjustment)}</div>
        </div>`).join('')}
      </div>
    </details>`;
  }).join('');
}
['im-cat', 'im-conf'].forEach(id => el(id).addEventListener('change', renderImpact));

// ─── PIPELINE ────────────────────────────────────────────────────────────────
function renderPipeline() {
  const p = state.pipeline;
  if (!p) { el('pipe-metrics').innerHTML = `<div class="empty">${state.loadedAt ? 'Pipeline data could not be loaded.' : 'Loading…'}</div>`; return; }
  const last = p.runs[0] || {};
  const ok = p.health.filter(h => h.status === 'ok').length;
  el('pipe-metrics').innerHTML = `
    <div class="mcard"><div class="lbl">Feeds healthy</div><div class="val ${ok < p.health.length ? '' : 'green'}">${ok}/${p.health.length}</div><div class="sub">latest run</div></div>
    <div class="mcard"><div class="lbl">Articles (latest run)</div><div class="val">${last.articles ?? '—'}</div><div class="sub">${last.new_articles ?? 0} new since previous run</div></div>
    <div class="mcard"><div class="lbl">Classified as events</div><div class="val green">${last.classified ?? '—'}</div><div class="sub">${last.signals_added ?? 0} new signals, ${last.duplicates ?? 0} merged · ${last.claude ? 'rules + Claude' : 'rules only'}</div></div>
    <div class="mcard"><div class="lbl">Run duration</div><div class="val small">${last.duration_s ?? '—'}s</div><div class="sub">finished ${esc(ago(last.finished))}</div></div>`;
  el('source-cards').innerHTML = p.health.map(h => `
    <div class="src-item">
      <div class="src-header-row"><div class="src-name">${esc(h.name)}</div><span class="status-dot status-${h.status === 'ok' ? 'ok' : h.status === 'warn' ? 'warn' : 'err'}" title="${esc(h.status)}"></span></div>
      <div class="src-meta">${esc(h.type)} · ${h.latency_ms !== null ? h.latency_ms + ' ms' : 'no response'} · newest item ${esc(ago(h.newest))}</div>
      <div class="src-stat">${h.items} items, ${h.fresh} from the last 48h</div>
      ${h.error ? `<div class="src-err">${esc(h.error)}</div>` : ''}
    </div>`).join('');
  el('run-history').innerHTML = `<table class="mini-table"><thead><tr><th>Finished</th><th>Articles</th><th>New</th><th>Events</th><th>Signals</th></tr></thead><tbody>${
    p.runs.slice(0, 12).map(r => `<tr><td>${esc(ago(r.finished))}</td><td>${r.articles}</td><td>${r.new_articles}</td><td>${r.classified}</td><td>${r.signals_added}</td></tr>`).join('')}</tbody></table>`;
  el('unclassified').innerHTML = (p.unclassified_sample || []).slice(0, 25).map(u =>
    `<div class="uncl"><span class="muted">${esc(u.publisher)}</span> ${esc(u.title)}</div>`).join('') || '<div class="empty">None.</div>';
}

// Browser port of pipeline/classify.py rule layer (patterns come from data/taxonomy.json).
function compileRules() {
  if (state.compiled || !state.taxonomy) return state.compiled;
  const rx = p => new RegExp(p, 'i');
  state.compiled = state.taxonomy.types.map(t => ({
    t, all: t.patterns.all.map(rx), none: t.patterns.none.map(rx), boost: t.patterns.boost.map(rx),
  }));
  return state.compiled;
}
const AMBIGUOUS = new Set(['target', 'meta', 'apple', 'amazon', 'ford', 'lilly', 'stat', 'kla', 'gm', 'real', 'vertex', 'abbott']);
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function findCompanies(text) {
  const out = [];
  const aliases = Object.entries(state.taxonomy.company_aliases).sort((a, b) => b[0].length - a[0].length);
  for (const [name, tkr] of aliases) {
    const re = AMBIGUOUS.has(name)
      ? new RegExp(`(?<![\\w])(${reEsc(name[0].toUpperCase() + name.slice(1))}|${reEsc(name.toUpperCase())})(?![\\w])`)
      : new RegExp(`(?<![\\w])${reEsc(name)}(?![\\w])`, 'i');
    if (re.test(text) && !out.includes(tkr)) out.push(tkr);
  }
  return out;
}
function classifyHeadline(title) {
  const MEGA = new Set(['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'NFLX']);
  const results = [];
  for (const c of compileRules() || []) {
    const failed = c.all.findIndex(r => !r.test(title));
    const veto = c.none.find(r => r.test(title));
    let tickers = [];
    if (c.t.id === 'currency_devaluation') {
      tickers = Object.entries(state.taxonomy.currency_aliases).filter(([n]) => new RegExp(`\\b${reEsc(n)}\\b`, 'i').test(title)).map(([, s]) => s);
    } else if (c.t.company_specific) {
      tickers = findCompanies(title);
      if (c.t.id.startsWith('megacap_earnings')) tickers = tickers.filter(t => MEGA.has(t));
    }
    const needsT = (c.t.company_specific && !c.t.default_tickers.length) || c.t.id.startsWith('megacap_earnings') || c.t.id === 'currency_devaluation';
    const boosts = c.boost.filter(r => r.test(title)).length;
    const score = Math.min(97, 62 + 6 * c.all.length + 8 * boosts);
    results.push({t: c.t, ok: failed < 0 && !veto && !(needsT && !tickers.length), failed, veto, tickers, score});
  }
  return results;
}
function runTry() {
  const title = el('try-input').value.trim();
  const out = el('try-output');
  if (!title) { out.innerHTML = ''; return; }
  if (!state.taxonomy) { out.innerHTML = '<div class="empty">Taxonomy not loaded.</div>'; return; }
  const res = classifyHeadline(title);
  const hit = res.filter(r => r.ok).sort((a, b) => b.score - a.score)[0];
  const near = res.filter(r => !r.ok && (r.failed < 0 || r.failed >= r.t.patterns.all.length - 1)).slice(0, 4);
  let html = '';
  if (hit) {
    const rows = (state.backtest?.rows || []).filter(r => r.type === hit.t.id);
    html += `<div class="try-hit"><div><b>${esc(hit.t.label)}</b> <span class="muted">${esc(hit.t.cat)} · match ${hit.score}</span>
      ${hit.tickers.length ? hit.tickers.map(t => `<span class="co-chip">${esc(t)}</span>`).join('') : ''}</div>
      <table class="mini-table"><thead><tr><th>Market</th><th>Direction</th><th>Best</th><th>Avg move</th><th>Hit</th><th>Conf</th><th>Events</th></tr></thead><tbody>${
        rows.map(r => `<tr><td>${esc(r.mkt)} <span class="muted">${esc(r.name)}</span></td><td>${dirPill(r.dir)}</td><td>${esc(r.optW || '—')}</td><td class="${moveCls(r.move)}">${sgn(r.move, 2)}%</td><td>${fmt(r.cons, 0)}%</td><td class="${confCls(r.conf)}">${esc(r.conf)}</td><td>${r.samples}</td></tr>`).join('')}</tbody></table></div>`;
  } else {
    html += '<div class="try-miss">No event type matched. Not every headline is a tradable event, which is intended.</div>';
  }
  if (near.length && !hit) {
    html += `<div class="fine">Near misses: ${near.map(r => `${esc(r.t.label)} (${r.veto ? 'vetoed by <code>' + esc(r.veto.source.slice(0, 60)) + '</code>' : r.failed >= 0 ? 'missing a required term' : 'no company/currency found'})`).join('; ')}</div>`;
  }
  out.innerHTML = html;
}
el('try-btn').addEventListener('click', runTry);
el('try-input').addEventListener('keydown', e => { if (e.key === 'Enter') runTry(); });

// ─── METHOD ──────────────────────────────────────────────────────────────────
function renderMethod() {
  const bt = state.backtest;
  el('method-body').innerHTML = `
    <div class="panel pad"><div class="ig-title">1 · Historical events</div>
      <p>Fed and ECB rate changes and 2s10s inversions come straight from FRED (effective dates mapped back to the announcement day). Everything else comes from a researched catalog in <code>data/catalog/*.json</code>, where every event has a source link. You can open each one from the Backtest page.</p>
      <p class="muted">${bt ? Object.values(bt.event_counts).reduce((a, b) => a + b, 0) + ' events across ' + Object.keys(bt.event_counts).length + ' types.' : ''}</p></div>
    <div class="panel pad"><div class="ig-title">2 · Event study</div>
      <p>${esc(bt?.method?.description || '')}</p>
      <p>Timing matters: a 2pm ET Fed decision hits US assets the same day but European and Asian stocks the next session. Each instrument's first reaction session is worked out from the event's US-hours timing and the exchange's region.</p></div>
    <div class="panel pad"><div class="ig-title">3 · Live signals</div>
      <p>Every 30 minutes a GitHub Action pulls ${state.pipeline?.health?.length ?? 'about 20'} public RSS feeds (Fed, ECB, FDA, CNBC, MarketWatch, FT, BBC, Al Jazeera, OilPrice, Yahoo Finance and Google News topic searches). A rule classifier maps headlines to event types and filters out speculation, previews and ongoing-story coverage. If an <code>ANTHROPIC_API_KEY</code> secret is set, Claude double-checks each batch, can veto false positives, and can catch events the rules missed.</p>
      <p>Articles about the same event are merged into one signal. Each signal records the last close before it appeared and is tracked afterwards. That tracking is the live track record.</p></div>
    <div class="panel pad warnbox"><div class="ig-title">Limitations, read these</div>
      <ul>
        <li>Daily closes only, so moves within the day are not measured. A "1d" move runs from the close before the event to the close of the first session that could react.</li>
        <li>Sample sizes are small (5–40 events per type). Most results are "Low" confidence, and the page says so instead of hiding it.</li>
        <li>The earnings, FDA, recall and AI-launch catalogs were partly picked because the stock moved a lot, so their magnitudes are inflated. These rows are flagged "selection-biased".</li>
        <li>Picking the best of 5 windows inflates significance. Confidence levels use a Bonferroni-adjusted p-value, but no out-of-sample test was done.</li>
        <li>Yahoo daily FX bars can sit about one day off around London midnight, so 1-day FX results are noisier than multi-day ones.</li>
        <li>Event dates were researched by hand. Report errors by editing the catalog JSON.</li>
        <li>This is research tooling, not investment advice.</li>
      </ul></div>
    <div class="panel pad"><div class="ig-title">Run it yourself</div>
<pre>python3 -m pipeline.run backtest
python3 -m pipeline.run ingest
python3 -m unittest discover -s tests
python3 -m http.server 8000</pre>
      <p>The first rebuilds <code>backtest.json</code> (about 3 minutes, mostly price downloads). The second fetches news into <code>signals.json</code>. Then open <code>localhost:8000</code>.</p>
      <p>Python 3.9+ standard library only. The optional Claude layer needs <code>pip install anthropic</code> and <code>ANTHROPIC_API_KEY</code> (in GitHub: Settings → Secrets → Actions).</p></div>`;
  el('score-weights').innerHTML = (state.taxonomy?.score_weights || []).map(w => `
    <div class="weight-row">
      <span class="weight-name">${esc(w.name)}</span>
      <div class="weight-bar-bg"><div class="weight-bar-fill" style="width:${w.pct}%"></div></div>
      <span class="weight-pct">${w.pct}%</span>
      <span class="weight-desc">${esc(w.desc)}</span>
    </div>`).join('');
}

// ─── init ────────────────────────────────────────────────────────────────────
showPage((location.hash || '#dashboard').slice(1));
loadAll();
setInterval(updateNav, 30e3);
setInterval(() => { if (document.visibilityState === 'visible') loadAll(); }, 5 * 60e3);
