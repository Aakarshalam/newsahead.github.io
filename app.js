// ─── STATE ────────────────────────────────────────────────────────────────────
const state = {
  feedItems: [],
  ingestCount: 0,
  signalCount: 0,
  alertCount: 0,
  lastIngest: null,
  dirChart: null,
  catChart: null,
  autoTimer: null,
  openBtRow: null,
  openImpact: null,
};

// ─── NAV ─────────────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const pg = link.dataset.page;
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    link.classList.add('active');
    document.getElementById('page-' + pg).classList.add('active');
    if (pg === 'backtest') renderBacktest();
    if (pg === 'pipeline') renderPipeline();
    if (pg === 'impact') renderImpact();
    if (pg === 'setup') renderSetup();
  });
});

// ─── HELPERS ─────────────────────────────────────────────────────────────────
const WINDOWS_LABELS = ['1h','4h','1d','3d','1w','2w','1m'];
const WINDOWS_H = [1,4,24,72,168,336,720];

function el(id) { return document.getElementById(id); }

function dirPill(d) {
  const map = {bull:'pill-bull',bear:'pill-bear',mix:'pill-mix'};
  const lbl = {bull:'↑ Bullish',bear:'↓ Bearish',mix:'~ Mixed'};
  return `<span class="pill ${map[d]||'pill-mix'}">${lbl[d]||d}</span>`;
}

function scoreCls(s) {
  if (s >= 80) return 'score-hi';
  if (s >= 60) return 'score-md';
  return 'score-lo';
}

function confCls(c) { return c === 'High' ? 'conf-h' : 'conf-m'; }

function timeAgo(ts) {
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return s + 's ago';
  return Math.round(s / 60) + 'm ago';
}

function fmtNum(n, d=1) { return Number(n).toFixed(d); }

function barHtml(pct, color) {
  return `<div class="bar-wrap"><div class="bar-bg"><div class="bar-fill" style="width:${Math.min(pct,100)}%;background:${color}"></div></div><span style="font-size:10px;min-width:34px;font-family:var(--mono)">${Math.round(pct)}%</span></div>`;
}

// ─── DASHBOARD METRICS ───────────────────────────────────────────────────────
function renderDashMetrics() {
  el('dash-metrics').innerHTML = `
    <div class="mcard"><div class="lbl">Articles ingested today</div><div class="val">${state.ingestCount.toLocaleString()}</div><div class="sub">across all 6 sources</div></div>
    <div class="mcard"><div class="lbl">Signals generated</div><div class="val green">${state.signalCount}</div><div class="sub">score ≥ 60</div></div>
    <div class="mcard"><div class="lbl">High-priority alerts</div><div class="val ${state.alertCount > 0 ? 'red' : ''}">${state.alertCount}</div><div class="sub">score ≥ 80, act now</div></div>
    <div class="mcard"><div class="lbl">Last ingested</div><div class="val" style="font-size:16px;padding-top:4px">${state.lastIngest ? timeAgo(state.lastIngest) : '—'}</div><div class="sub"><span class="pulse-dot sm"></span> live</div></div>
  `;
}

// ─── SIGNAL FEED ─────────────────────────────────────────────────────────────
function renderFeed() {
  const mktF = el('dash-mkt-filter') ? el('dash-mkt-filter').value : '';
  const scoreF = parseInt(el('dash-score-filter') ? el('dash-score-filter').value : '0') || 0;
  const items = state.feedItems.filter(f => {
    if (mktF && f.mkt !== mktF) return false;
    if (f.score < scoreF) return false;
    return true;
  });
  const feedEl = el('signal-feed');
  if (!items.length) {
    feedEl.innerHTML = '<div style="padding:24px;text-align:center;font-size:12px;color:var(--text3)">No signals yet — click "Simulate ingest" or wait for auto-ingest.</div>';
    return;
  }
  feedEl.innerHTML = items.slice(0, 25).map((f, i) => `
    <div class="signal-item" onclick="openDrawer(${state.feedItems.indexOf(f)})">
      <div>
        <div class="si-time">${f.timeLabel}</div>
        <div class="si-src">${f.source}</div>
      </div>
      <div>
        <div class="si-title">${f.title}</div>
        <div class="si-tags">
          <span class="tag tag-cat">${f.cat}</span>
          <span class="tag tag-geo">${f.geo}</span>
          <span class="tag">${f.mkt}</span>
          ${dirPill(f.dir)}
          ${f.companies.slice(0,3).map(c => `<span class="tag tag-co">${c}</span>`).join('')}
        </div>
      </div>
      <div style="text-align:right">
        <div class="score-num ${scoreCls(f.score)}">${f.score}</div>
        <div class="score-win">${f.window}</div>
      </div>
    </div>
  `).join('');
}

// ─── SIGNAL DRAWER ───────────────────────────────────────────────────────────
window.openDrawer = function(idx) {
  const f = state.feedItems[idx];
  if (!f) return;
  el('drawer-title').textContent = f.event_type.replace(/_/g,' ').toUpperCase();
  const bd = f.score_breakdown;
  const total = (bd.event_match*0.30 + bd.sentiment*0.25 + bd.source_cred*0.20 + bd.novelty*0.15 + bd.geo_scope*0.10).toFixed(1);
  const coChips = f.companies.map((c,i) => `<span class="co-chip ${i<3?'primary':''}">${c}</span>`).join('');
  const winBoxes = WINDOWS_LABELS.map((w,i) => {
    const bt = BACKTEST_DATA.find(r => r.event_type === f.event_type || r.mkt === f.mkt);
    const v = bt ? (bt.windows[WINDOWS_H[i]] || 0) : (Math.random()*3+0.5);
    const isBest = w === f.window;
    return `<div class="win-box${isBest?' best':''}"><div class="wl">${w}</div><div class="wv">${fmtNum(v)}%</div></div>`;
  }).join('');

  el('drawer-body').innerHTML = `
    <div class="drawer-body-grid">
      <div>
        <div class="d-section-title">Signal details</div>
        <div class="d-row"><span class="d-key">Headline</span><span class="d-val">${f.title}</span></div>
        <div class="d-row"><span class="d-key">Category</span><span class="d-val">${f.cat}</span></div>
        <div class="d-row"><span class="d-key">Geography</span><span class="d-val">${f.geo}</span></div>
        <div class="d-row"><span class="d-key">Market</span><span class="d-val">${f.mkt}</span></div>
        <div class="d-row"><span class="d-key">Direction</span><span class="d-val">${dirPill(f.dir)}</span></div>
        <div class="d-row"><span class="d-key">Optimal window</span><span class="d-val" style="color:var(--accent)">${f.window}</span></div>
        <div class="d-row"><span class="d-key">Source</span><span class="d-val">${f.source}</span></div>
        <div style="margin-top:12px">
          <div class="d-section-title">Companies affected</div>
          <div style="margin-top:6px">${coChips}</div>
        </div>
      </div>
      <div>
        <div class="d-section-title">Score decomposition</div>
        <div class="score-decomp" style="padding:0">
          <div style="font-size:24px;font-weight:700;font-family:var(--mono);color:var(--accent);margin-bottom:12px">${total} / 100</div>
          ${[
            {k:'Event match',v:bd.event_match,w:'30%'},
            {k:'Sentiment',v:bd.sentiment,w:'25%'},
            {k:'Source cred.',v:bd.source_cred,w:'20%'},
            {k:'Novelty',v:bd.novelty,w:'15%'},
            {k:'Geo scope',v:bd.geo_scope,w:'10%'}
          ].map(r => `
            <div class="score-bar-row">
              <span class="score-bar-label">${r.k} <span style="color:var(--text3);font-size:9px">(${r.w})</span></span>
              <div class="score-bar-bg"><div class="score-bar-fill" style="width:${r.v}%"></div></div>
              <span class="score-bar-val">${r.v}</span>
            </div>
          `).join('')}
        </div>
      </div>
      <div>
        <div class="d-section-title">Window analysis (avg move %)</div>
        <div class="win-row">${winBoxes}</div>
        <div style="margin-top:16px">
          <div class="d-section-title">Schema output</div>
          <div style="font-family:var(--mono);font-size:10px;line-height:1.9;color:var(--text2)">
            <span style="color:#7eb3ff">event_type</span>: <span style="color:var(--green)">"${f.event_type}"</span><br>
            <span style="color:#7eb3ff">signal_score</span>: <span style="color:var(--amber)">${f.score}</span><br>
            <span style="color:#7eb3ff">direction</span>: <span style="color:var(--green)">"${f.dir.toUpperCase()}"</span><br>
            <span style="color:#7eb3ff">opt_window</span>: <span style="color:var(--green)">"${f.window}"</span><br>
            <span style="color:#7eb3ff">geo</span>: <span style="color:var(--green)">"${f.geo}"</span><br>
            <span style="color:#7eb3ff">novelty</span>: <span style="color:var(--green)">true</span>
          </div>
        </div>
      </div>
    </div>
  `;
  el('signal-drawer').classList.add('open');
};

window.closeDrawer = function() {
  el('signal-drawer').classList.remove('open');
};

// ─── MINI CHARTS ─────────────────────────────────────────────────────────────
function renderDirChart() {
  const bull = state.feedItems.filter(f => f.dir === 'bull').length;
  const bear = state.feedItems.filter(f => f.dir === 'bear').length;
  const mix = state.feedItems.filter(f => f.dir === 'mix').length;
  if (state.dirChart) state.dirChart.destroy();
  state.dirChart = new Chart(el('dir-chart'), {
    type: 'doughnut',
    data: {
      labels: ['Bullish','Bearish','Mixed'],
      datasets: [{data: [bull||1, bear||1, mix||1], backgroundColor: ['#3fcf8e','#f76060','#f5a623'], borderWidth: 0, hoverOffset: 4}]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, cutout: '72%' }
  });
}

function renderCatChart() {
  const cats = {};
  state.feedItems.forEach(f => { cats[f.cat] = (cats[f.cat]||0)+1; });
  const labels = Object.keys(cats);
  const data = labels.map(k => cats[k]);
  if (state.catChart) state.catChart.destroy();
  state.catChart = new Chart(el('cat-chart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{ data, backgroundColor: '#4f8ef7', borderRadius: 4, borderWidth: 0 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#5a6070', font: { size: 10 } } },
        y: { grid: { display: false }, ticks: { color: '#9aa0b0', font: { size: 10 } } }
      }
    }
  });
}

// ─── INGEST ──────────────────────────────────────────────────────────────────
function ingestArticle() {
  const tmpl = FEED_TEMPLATES[Math.floor(Math.random() * FEED_TEMPLATES.length)];
  const mins = Math.floor(Math.random() * 5);
  const article = { ...tmpl, timeLabel: mins === 0 ? 'just now' : `${mins}m ago`, timestamp: Date.now() };
  state.feedItems.unshift(article);
  if (state.feedItems.length > 60) state.feedItems.pop();
  state.ingestCount += Math.floor(Math.random() * 15) + 8;
  if (article.score >= 60) state.signalCount++;
  if (article.score >= 80) state.alertCount++;
  state.lastIngest = Date.now();
  renderDashMetrics();
  renderFeed();
  renderDirChart();
  renderCatChart();
  // update pipeline metrics if on that page
  renderPipeMetrics();
}

function autoIngest() {
  ingestArticle();
  const delay = 9000 + Math.random() * 8000;
  state.autoTimer = setTimeout(autoIngest, delay);
}

el('sim-btn').addEventListener('click', ingestArticle);
el('dash-mkt-filter').addEventListener('change', renderFeed);
el('dash-score-filter').addEventListener('change', renderFeed);

// ─── BACKTEST ────────────────────────────────────────────────────────────────
function renderBtMetrics() {
  const rows = getFilteredBt();
  const avgCons = rows.length ? Math.round(rows.reduce((a,r) => a+r.cons, 0) / rows.length) : 0;
  const maxMove = rows.length ? Math.max(...rows.map(r => r.move)) : 0;
  const hconf = rows.filter(r => r.conf === 'High').length;
  el('bt-metrics').innerHTML = `
    <div class="mcard"><div class="lbl">Events indexed</div><div class="val">${rows.length}</div><div class="sub">filtered results</div></div>
    <div class="mcard"><div class="lbl">Avg consistency</div><div class="val green">${avgCons}%</div><div class="sub">direction held</div></div>
    <div class="mcard"><div class="lbl">Best avg move</div><div class="val">${fmtNum(maxMove)}%</div><div class="sub">at optimal window</div></div>
    <div class="mcard"><div class="lbl">High-conf signals</div><div class="val green">${hconf}</div><div class="sub">ready to trade</div></div>
  `;
}

function getFilteredBt() {
  const cat = el('bt-cat').value;
  const mkt = el('bt-mkt').value;
  const conf = el('bt-conf').value;
  const q = el('bt-search').value.toLowerCase();
  return BACKTEST_DATA.filter(r => {
    if (cat && r.cat !== cat) return false;
    if (mkt && r.mkt !== mkt) return false;
    if (conf === 'High' && r.conf !== 'High') return false;
    if (conf === 'Med' && r.conf === 'Low') return false;
    if (q && !r.event.toLowerCase().includes(q) && !r.cat.toLowerCase().includes(q) && !r.geo.toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderBacktest() {
  renderBtMetrics();
  const rows = getFilteredBt();
  const tbody = el('bt-tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:24px;color:var(--text3)">No results match filters.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r, i) => {
    const barColor = r.dir==='bull' ? '#3fcf8e' : r.dir==='bear' ? '#f76060' : '#f5a623';
    const coList = r.companies.slice(0, 5).map(c => `<span class="co-chip">${c}</span>`).join('');
    return `
      <tr onclick="toggleBtRow(${i})" style="cursor:pointer">
        <td class="bt-expander" id="bt-arr-${i}">▶</td>
        <td style="font-weight:500;font-size:12px">${r.event}</td>
        <td><span class="tag tag-geo">${r.geo}</span></td>
        <td style="font-size:11px;color:var(--text2)">${r.mkt}</td>
        <td><div class="co-list">${coList}</div></td>
        <td>${dirPill(r.dir)}</td>
        <td>${barHtml(r.cons, barColor)}</td>
        <td style="font-weight:600;font-family:var(--mono);font-size:12px">${fmtNum(r.move)}%</td>
        <td style="font-family:var(--mono);font-size:11px;color:var(--accent)">${r.optW}</td>
        <td style="font-size:11px;color:var(--text3);font-family:var(--mono)">${r.samples}</td>
        <td class="${confCls(r.conf)}">${r.conf}</td>
      </tr>
      <tr class="detail-row" id="bt-detail-${i}" style="display:none">
        <td colspan="11">
          <div class="detail-expand">
            <div>
              <div class="d-section-title">Window analysis — avg move %</div>
              <div class="win-row" style="margin-top:8px">
                ${WINDOWS_LABELS.map((w,j) => {
                  const v = r.windows[WINDOWS_H[j]] || 0;
                  return `<div class="win-box${w===r.optW?' best':''}"><div class="wl">${w}</div><div class="wv">${fmtNum(v)}%</div></div>`;
                }).join('')}
              </div>
              <div style="margin-top:12px;font-size:11px;color:var(--text3)">Signal half-life: <span style="color:var(--text2)">${r.halfLife}</span></div>
              <div style="margin-top:4px;font-size:11px;color:var(--text3)">Sample size: <span style="color:var(--text2)">${r.samples} historical occurrences</span></div>
            </div>
            <div>
              <div class="d-section-title">Geography detail</div>
              <div style="font-size:11px;color:var(--text2);line-height:1.6;margin-top:6px">${r.geo_detail}</div>
              <div style="margin-top:12px">
                <div class="d-section-title">All companies / instruments</div>
                <div style="margin-top:6px">${r.companies.map(c => `<span class="co-chip">${c}</span>`).join('')}</div>
              </div>
              <div style="margin-top:10px;font-size:11px;color:var(--text3)">${r.ticker_note}</div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

window.toggleBtRow = function(i) {
  const det = el('bt-detail-' + i);
  const arr = el('bt-arr-' + i);
  const isOpen = det.style.display !== 'none';
  document.querySelectorAll('[id^="bt-detail-"]').forEach(r => r.style.display = 'none');
  document.querySelectorAll('.bt-expander').forEach(a => a.textContent = '▶');
  if (!isOpen) { det.style.display = 'table-row'; arr.textContent = '▼'; }
};

['bt-cat','bt-mkt','bt-conf'].forEach(id => el(id).addEventListener('change', renderBacktest));
el('bt-search').addEventListener('input', renderBacktest);

// ─── PIPELINE ────────────────────────────────────────────────────────────────
function renderPipeMetrics() {
  const pm = el('pipe-metrics');
  if (!pm) return;
  const total = state.ingestCount || 1240;
  const sigs = state.signalCount || 47;
  pm.innerHTML = `
    <div class="mcard"><div class="lbl">Articles ingested</div><div class="val">${total.toLocaleString()}</div><div class="sub">today</div></div>
    <div class="mcard"><div class="lbl">Classified signals</div><div class="val green">${sigs}</div><div class="sub">score ≥ 60</div></div>
    <div class="mcard"><div class="lbl">Novelty rejected</div><div class="val">${Math.round(total * 0.61).toLocaleString()}</div><div class="sub">follow-on / noise</div></div>
    <div class="mcard"><div class="lbl">Avg classify latency</div><div class="val" style="font-size:16px;padding-top:4px">~140ms</div><div class="sub">per article</div></div>
  `;
}

function renderPipeline() {
  renderPipeMetrics();

  // Sources
  el('source-cards').innerHTML = SOURCES.map(s => {
    const dotCls = s.status==='ok' ? 'status-ok' : s.status==='warn' ? 'status-warn' : 'status-err';
    const pct = Math.round(s.rph / s.maxRph * 100);
    const fillColor = pct > 85 ? '#f76060' : pct > 60 ? '#f5a623' : '#3fcf8e';
    return `
      <div class="src-item">
        <div class="src-header-row">
          <div class="src-name">${s.name}</div>
          <span class="status-dot ${dotCls}"></span>
        </div>
        <div class="src-meta">${s.type} · latency ${s.latency}</div>
        <div class="src-stat">${s.rph.toLocaleString()} articles/hr · ${s.cats.join(', ')}</div>
        <div style="font-size:10px;color:var(--text3);margin-top:4px">${s.note}</div>
        <div class="src-prog"><div class="src-prog-fill" style="width:${pct}%;background:${fillColor}"></div></div>
      </div>
    `;
  }).join('');

  // Classifier bars
  const clsItems = [
    {lbl:'Economic events',acc:91,color:'#4f8ef7'},
    {lbl:'Geopolitical events',acc:88,color:'#4f8ef7'},
    {lbl:'Industrial / commodity',acc:84,color:'#4f8ef7'},
    {lbl:'Technology',acc:79,color:'#4f8ef7'},
    {lbl:'Political',acc:74,color:'#4f8ef7'},
    {lbl:'Social / ESG',acc:76,color:'#4f8ef7'},
    {lbl:'Sentiment (bull/bear)',acc:87,color:'#3fcf8e'},
    {lbl:'Novelty filter',acc:83,color:'#9b8cf7'},
  ];
  el('classifier-bars').innerHTML = clsItems.map(c => `
    <div class="cls-row">
      <span class="cls-lbl">${c.lbl}</span>
      <div class="cls-bar-bg"><div class="cls-bar-fill" style="width:${c.acc}%;background:${c.color}"></div></div>
      <span class="cls-val">${c.acc}%</span>
    </div>
  `).join('');

  // Show last item in schema if available
  if (state.feedItems.length) showPipelineSchema(state.feedItems[0]);
  else showPipelineSchema(FEED_TEMPLATES[0]);
}

function showPipelineSchema(f) {
  const now = new Date().toISOString();
  const evtId = 'EVT_' + Math.random().toString(36).substr(2,10).toUpperCase();
  const analogs = Math.floor(Math.random()*18)+6;
  el('schema-output').innerHTML = `
    <div class="schema-wrap">
      <div class="schema-code">
<span class="k">event_id</span>:        <span class="v">"${evtId}"</span><br>
<span class="k">timestamp_utc</span>:   <span class="v">"${now}"</span><br>
<span class="k">event_type</span>:      <span class="v">"${f.event_type}"</span><br>
<span class="k">category</span>:        <span class="v">"${f.cat}"</span><br>
<span class="k">geography</span>:       <span class="v">"${f.geo}"</span><br>
<span class="k">market</span>:          <span class="v">"${f.mkt}"</span><br>
<span class="k">direction</span>:       <span class="v">"${f.dir === 'bull' ? 'BULLISH' : f.dir === 'bear' ? 'BEARISH' : 'MIXED'}"</span><br>
<span class="k">signal_score</span>:    <span class="n">${f.score}</span><br>
<span class="k">score_breakdown</span>: <span class="s">{event_match: ${f.score_breakdown.event_match}, sentiment: ${f.score_breakdown.sentiment}, source_cred: ${f.score_breakdown.source_cred}, novelty: ${f.score_breakdown.novelty}, geo_scope: ${f.score_breakdown.geo_scope}}</span><br>
<span class="k">optimal_window</span>: <span class="v">"${f.window}"</span><br>
<span class="k">sectors</span>:         <span class="v">[${f.sectors.map(s=>`"${s}"`).join(', ')}]</span><br>
<span class="k">companies</span>:       <span class="v">[${f.companies.slice(0,5).map(c=>`"${c}"`).join(', ')}]</span><br>
<span class="k">source</span>:          <span class="v">"${f.source}"</span><br>
<span class="k">novelty_flag</span>:    <span class="v">true</span><br>
<span class="k">backtest_matched</span>: <span class="v">true</span>  <span style="color:var(--text3)">// ${analogs} historical analogs</span>
      </div>
    </div>
  `;
}

el('pipe-sim-btn').addEventListener('click', () => {
  ingestArticle();
  showPipelineSchema(state.feedItems[0]);
});

// ─── IMPACT MAP ───────────────────────────────────────────────────────────────
function renderImpact() {
  const cat = el('im-cat').value;
  const geo = el('im-geo').value;

  const rows = BACKTEST_DATA.filter(r => {
    if (cat && r.cat !== cat) return false;
    if (geo && !r.geo.includes(geo)) return false;
    return true;
  });

  el('impact-cards').innerHTML = rows.map((r, i) => {
    const winBoxes = WINDOWS_LABELS.map((w, j) => {
      const v = r.windows[WINDOWS_H[j]] || 0;
      return `<div class="win-box${w===r.optW?' best':''}"><div class="wl">${w}</div><div class="wv">${fmtNum(v)}%</div></div>`;
    }).join('');

    return `
      <div class="impact-card">
        <div class="impact-header" onclick="toggleImpact(${i})">
          <div>
            <div class="impact-event">${r.event}</div>
            <div class="impact-meta">
              <span class="tag tag-cat">${r.cat}</span>
              <span class="tag tag-geo">${r.geo}</span>
              <span class="tag">${r.mkt}</span>
              ${dirPill(r.dir)}
              <span class="tag" style="font-family:var(--mono)">${r.samples} samples</span>
            </div>
          </div>
          <div class="impact-score">
            <div class="is-num">${fmtNum(r.move)}%</div>
            <div class="is-lbl">avg move @ ${r.optW}</div>
            <div style="margin-top:4px">${barHtml(r.cons, r.dir==='bull'?'#3fcf8e':r.dir==='bear'?'#f76060':'#f5a623')}</div>
          </div>
        </div>
        <div class="impact-body" id="im-body-${i}">
          <div class="impact-grid">
            <div>
              <div class="ig-title">Sectors & companies affected</div>
              ${r.sectors.map(s => `<div style="font-size:11px;color:var(--text2);margin-bottom:4px;font-weight:500">${s}</div>`).join('')}
              <div style="margin-top:8px">${r.companies.map(c => `<span class="co-chip">${c}</span>`).join('')}</div>
              <div style="margin-top:8px;font-size:10px;color:var(--text3);font-style:italic">${r.ticker_note}</div>
            </div>
            <div>
              <div class="ig-title">Geographic impact breakdown</div>
              <div style="font-size:11px;color:var(--text2);line-height:1.7">${r.geo_detail}</div>
            </div>
            <div>
              <div class="ig-title">Time windows (avg move %)</div>
              <div class="win-row">${winBoxes}</div>
              <div style="margin-top:10px;font-size:11px;color:var(--text3)">Half-life: <span style="color:var(--text2)">${r.halfLife}</span></div>
              <div style="margin-top:4px;font-size:11px;color:var(--text3)">Confidence: <span class="${confCls(r.conf)}">${r.conf}</span></div>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

window.toggleImpact = function(i) {
  const body = el('im-body-' + i);
  body.classList.toggle('open');
};

['im-cat','im-geo'].forEach(id => el(id).addEventListener('change', renderImpact));

// ─── SETUP ───────────────────────────────────────────────────────────────────
function renderSetup() {
  const apis = [
    {name:'NewsAPI',plan:'free/paid',url:'https://newsapi.org/register',desc:'General news aggregator. Covers 80,000+ sources. Required for Political, Technology, Social categories.',key:'newsapi_key'},
    {name:'GDELT 2.0',plan:'free',url:'https://www.gdeltproject.org',desc:'Free. No key required. Geopolitical event stream updated every 15 minutes. Best source for conflict signals.',key:'gdelt_key'},
    {name:'Finnhub',plan:'free/paid',url:'https://finnhub.io/register',desc:'Real-time financial news, earnings calendars, press releases, and SEC filing alerts. Free tier is sufficient to start.',key:'finnhub_key'},
    {name:'Alpha Vantage',plan:'free/paid',url:'https://www.alphavantage.co/support/#api-key',desc:'Macro news, sentiment scores, and fundamental data. Free tier limited to 25 calls/day.',key:'alphavantage_key'},
    {name:'EIA API',plan:'free',url:'https://www.eia.gov/opendata/register.php',desc:'US Energy Information Administration. Free key for oil, gas, electricity data. Pairs with OPEC news.',key:'eia_key'},
    {name:'Polygon.io',plan:'paid',url:'https://polygon.io/pricing',desc:'Real-time price data for backtesting — equities, forex, crypto. Required to compute real historical price reactions.',key:'polygon_key'},
  ];

  el('setup-grid').innerHTML = apis.map(a => {
    const saved = localStorage.getItem(a.key) || '';
    return `
      <div class="setup-card">
        <div class="setup-card-header">
          <span class="setup-card-name">${a.name}</span>
          <span class="setup-badge ${a.plan==='free'?'free':'paid'}">${a.plan}</span>
        </div>
        <div class="setup-desc">${a.desc}</div>
        <div class="setup-input-row">
          <input type="password" placeholder="Paste API key here..." value="${saved}" id="key-${a.key}" onchange="saveKey('${a.key}', this.value)"/>
          <button class="btn-ghost" onclick="toggleKeyVis('key-${a.key}')">Show</button>
        </div>
        <div class="setup-url"><a href="${a.url}" target="_blank">${a.url} →</a></div>
      </div>
    `;
  }).join('');

  // Score weights
  el('score-weights').innerHTML = SCORE_WEIGHTS.map(w => `
    <div class="weight-row">
      <span class="weight-name">${w.name}</span>
      <div class="weight-bar-bg"><div class="weight-bar-fill" style="width:${w.pct}%"></div></div>
      <span class="weight-pct">${w.pct}%</span>
      <span class="weight-desc">${w.desc}</span>
    </div>
  `).join('');
}

window.saveKey = function(key, val) {
  localStorage.setItem(key, val);
};

window.toggleKeyVis = function(id) {
  const inp = el(id);
  inp.type = inp.type === 'password' ? 'text' : 'password';
};

// ─── INIT ─────────────────────────────────────────────────────────────────────
renderDashMetrics();
renderFeed();
renderDirChart();
renderCatChart();
renderBtMetrics();

// Seed a few items immediately
setTimeout(() => {
  ingestArticle();
  setTimeout(() => { ingestArticle(); autoIngest(); }, 1200);
}, 600);

// Refresh time display
setInterval(() => {
  if (state.lastIngest) {
    const ago = timeAgo(state.lastIngest);
    const el2 = document.querySelector('#dash-metrics .mcard:last-child .val');
    if (el2) el2.textContent = ago;
  }
}, 5000);
