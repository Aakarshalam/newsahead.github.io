"""Real-browser checks of the front end (headless Firefox).

Serves the repo plus a generated harness page that loads the real index.html/app.js
with fixture data (including hostile strings), drives the UI, and POSTs a report
back to this script. Checks:
  * JS/Python parity: the in-browser classifier (a port of pipeline/classify.py's
    rule layer) returns the same event type as Python for every test headline.
  * XSS: hostile feed titles / publishers / URLs never execute or produce
    javascript: links, across dashboard, drawer, backtest detail and pipeline pages.
  * No uncaught JS errors while rendering every page.

  python3 tests/browser_check.py          # exits non-zero on failure
Requires `firefox` on PATH (snap Firefox works: files go under ~/snap/firefox/common).
"""
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import classify  # noqa: E402

HOSTILE = '<img src=x onerror="window.__pwned=(window.__pwned||0)+1">'
HOSTILE_URL = "javascript:window.__pwned=99"


def headlines():
    from tests.test_classify import NEGATIVE, POSITIVE
    out = [t for t, _ in POSITIVE] + list(NEGATIVE)
    with open(os.path.join(ROOT, "tests", "data", "holdout_gold.json")) as f:
        out += [x["title"] for x in json.load(f)["items"]]
    return out


def load(name):
    with open(os.path.join(ROOT, "data", name)) as f:
        return json.load(f)


def hostile_fixture():
    sig = load("signals.json")
    bt = load("backtest.json")
    s = dict(sig["signals"][0]) if sig["signals"] else None
    if s:
        s.update(id="evil", title="Evil " + HOSTILE, publisher="<script>window.__pwned=2</script>", url=HOSTILE_URL,
                 also_reported_by=[HOSTILE], companies=[HOSTILE], rule_hits=[HOSTILE])
        sig = dict(sig, signals=[s] + sig["signals"])
    row = dict(bt["rows"][0])
    row["events"] = [dict(row["events"][0], headline="Evil " + HOSTILE, source=HOSTILE_URL)] + row["events"][1:]
    row["note"] = HOSTILE
    bt = dict(bt, rows=[row] + bt["rows"][1:])
    pipe = load("pipeline.json")
    pipe = dict(pipe, unclassified_sample=[dict(title=HOSTILE, publisher=HOSTILE, feed="x")] + pipe["unclassified_sample"])
    return {"data/signals.json": sig, "data/backtest.json": bt, "data/pipeline.json": pipe,
            "data/taxonomy.json": load("taxonomy.json"), "data/signal_history.json": load("signal_history.json"),
            "data/verification.json": load("verification.json") if os.path.exists(os.path.join(ROOT, "data", "verification.json")) else {}}


DRIVER = r"""
<script>
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message || e)));
window.addEventListener('unhandledrejection', e => window.__errors.push('promise: ' + String(e.reason)));
</script>
"""

AFTER = r"""
<script>
(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  await wait(50);
  const pages = ['dashboard', 'backtest', 'impact', 'pipeline', 'method'];
  for (const p of pages) { showPage(p); await wait(20); }
  showPage('dashboard'); document.querySelector('.signal-item')?.click(); await wait(20);
  showPage('backtest'); document.querySelector('#bt-tbody .bt-row')?.click(); await wait(20);
  showPage('impact'); document.querySelectorAll('details.impact-card').forEach(d => d.open = true); await wait(20);
  showPage('pipeline');
  document.getElementById('try-input').value = 'OPEC+ agrees surprise cut'; document.getElementById('try-btn').click();
  await wait(20);
  const heads = __HEADLINES__;
  const parity = heads.map(t => {
    const r = classifyHeadline(t).filter(x => x.ok).sort((a, b) => b.score - a.score)[0];
    return r ? r.t.id : 'none';
  });
  const jsLinks = [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => /^\s*javascript:/i.test(h));
  const report = {parity, errors: window.__errors, pwned: window.__pwned || 0, jsLinks,
                  imgOnerror: document.querySelectorAll('img[onerror]').length,
                  scripts: document.querySelectorAll('main script').length};
  await fetch('/__report', {method: 'POST', body: JSON.stringify(report)});
})();
</script>
"""


def build_page(path):
    html = open(os.path.join(ROOT, "index.html")).read()
    html = html.replace('href="style.css"', 'href="/style.css"')
    fixture = json.dumps(hostile_fixture()).replace("</", "<\\/")
    html = html.replace("<head>", "<head>" + DRIVER, 1)
    html = html.replace('<script src="app.js"></script>',
                        f"<script>window.__NA_FIXTURE={fixture};</script>\n<script src=\"/app.js\"></script>\n"
                        + AFTER.replace("__HEADLINES__", json.dumps(headlines()).replace("</", "<\\/")))
    with open(path, "w") as f:
        f.write(html)


def main():
    report = {}
    done = threading.Event()
    tmp = tempfile.mkdtemp()
    build_page(os.path.join(tmp, "harness.html"))

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/harness.html"):
                body = open(os.path.join(tmp, "harness.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            report.update(json.loads(self.rfile.read(n)))
            self.send_response(204)
            self.end_headers()
            done.set()

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    prof = os.path.expanduser("~/snap/firefox/common/na_browser_check")
    os.makedirs(prof, exist_ok=True)
    proc = subprocess.Popen(["firefox", "--headless", "--no-remote", "--profile", prof, f"http://127.0.0.1:{port}/harness.html"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = done.wait(120)
    proc.terminate()
    srv.shutdown()
    shutil.rmtree(prof, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if not ok:
        print("FAIL: browser never reported back (JS crashed before the harness finished?)")
        return 1

    heads = headlines()
    py = []
    for t in heads:
        c = classify.classify_rules(dict(id="x", title=t, summary=""))
        py.append(c["type"] if c else "none")
    mism = [(t, p, j) for t, p, j in zip(heads, py, report["parity"]) if p != j]
    fails = 0
    print(f"parity: {len(heads) - len(mism)}/{len(heads)} headlines classified identically by Python and JS")
    for t, p, j in mism[:20]:
        print(f"   MISMATCH py={p} js={j} | {t[:90]}")
    fails += bool(mism)
    print(f"xss: pwned={report['pwned']} javascript:links={len(report['jsLinks'])} img[onerror]={report['imgOnerror']}")
    fails += bool(report["pwned"] or report["jsLinks"] or report["imgOnerror"])
    print(f"js errors: {report['errors'] or 'none'}")
    fails += bool(report["errors"])
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
