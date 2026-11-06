"""Build tests/.ui/index.html: the real page with data/*.json inlined, so a
headless browser screenshot captures fully rendered pages (no fetch timing).

  python3 tests/make_ui_fixture.py && python3 -m http.server 8765
  firefox --headless --screenshot out.png "http://localhost:8765/tests/.ui/index.html#backtest"
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out_dir = os.path.join(ROOT, "tests", ".ui")
os.makedirs(out_dir, exist_ok=True)
fixture = {}
for name in ("signals", "backtest", "pipeline", "taxonomy", "signal_history"):
    with open(os.path.join(ROOT, "data", name + ".json")) as f:
        fixture[f"data/{name}.json"] = json.load(f)
# ?act=bt-expand | drawer | try:<headline>  simulates a user interaction for screenshots
ACTIONS = """<script>
const act = new URLSearchParams(location.search).get('act') || '';
setTimeout(() => {
  if (act === 'bt-expand') document.querySelector('#bt-tbody .bt-row:nth-of-type(1)')?.click();
  if (act.startsWith('bt-search:')) { const s = document.getElementById('bt-search'); s.value = act.slice(10); s.dispatchEvent(new Event('input')); document.querySelector('#bt-tbody .bt-row')?.click(); }
  if (act === 'drawer') { document.querySelector('.signal-item')?.click(); document.getElementById('signal-drawer').scrollIntoView(); }
  if (act.startsWith('try:')) { document.getElementById('try-input').value = act.slice(4); document.getElementById('try-btn').click(); }
}, 0);
</script>"""
html = open(os.path.join(ROOT, "index.html")).read()
html = html.replace('href="style.css"', 'href="../../style.css"')
html = html.replace('<script src="app.js"></script>',
                    "<script>window.__NA_FIXTURE=" + json.dumps(fixture).replace("</", "<\\/") +
                    ";Chart&&(Chart.defaults.animation=false);</script>\n<script src=\"../../app.js\"></script>\n" + ACTIONS)
with open(os.path.join(out_dir, "index.html"), "w") as f:
    f.write(html)
print("wrote", os.path.join(out_dir, "index.html"))
