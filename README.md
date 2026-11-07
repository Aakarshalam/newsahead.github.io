# NewsAlpha

News-driven market event research. Public news feeds are classified into event types (Fed hikes, OPEC cuts, tariffs, FDA decisions, …). Each signal is scored against an event-study backtest of how markets reacted to past events of that type, and its prices are tracked afterwards.

The site is static (GitHub Pages). A scheduled GitHub Action runs the Python pipeline and commits JSON into `data/`, which the page reads. This setup is needed because most news and price sources don't allow browser (CORS) requests.

```
news RSS ─► pipeline/sources.py ─► classify.py (rules [+ Claude]) ─► run.py (score, cluster, track) ─► data/signals.json
FRED + data/catalog ─► events.py ─► backtest.py (Yahoo daily prices) ─► data/backtest.json
                                                         index.html + app.js read data/*.json
```

## Run locally

Requires Python 3.9+ with the standard library only.

```bash
python3 -m pipeline.run backtest     # ~3 min first time (downloads prices, cached 12h)
python3 -m pipeline.run ingest       # fetch feeds, classify, score, track
python3 -m unittest discover -s tests            # NETWORK=1 also checks FRED dates
python3 -m http.server 8000                      # open http://localhost:8000
python3 -m pipeline.run classify "OPEC+ agrees surprise output cut"   # debug one headline
```

## Testing and verification

| Layer | Command | What it checks |
|---|---|---|
| Unit and offline (73 tests, ~8s) | `python3 -m unittest discover -s tests` | stats math, timing alignment, feed-parsing fuzz, failure injection, catalog integrity, Monte Carlo calibration, mocked Claude layer, end-to-end ingest |
| Network | `NETWORK=1 python3 -m unittest discover -s tests` | FRED-derived Fed/ECB dates match known decision days |
| Held-out classifier | `python3 -m pipeline.evaluate` | blind two-annotator gold set (`tests/data/holdout_gold.json`); rules may be tuned on `dev` only, `test` is reported |
| Data and method | `python3 -m pipeline.verify` | Yahoo vs FRED prices, event-day volatility (date check), placebo calibration on real prices, catalog link health → `data/verification.json` |
| Browser | `python3 tests/browser_check.py` | JS/Python classifier parity, XSS with hostile feed data, uncaught JS errors (headless Firefox) |

CI runs the unit tests on every pipeline run and the full verification weekly. Results appear on the Method page.

## Deploy

1. Push to GitHub with Pages serving the `master` branch root.
2. Under Actions, enable workflows. `.github/workflows/pipeline.yml` ingests every 30 minutes and rebuilds the backtest each weekday after the US close.
3. Optional: add an `ANTHROPIC_API_KEY` repository secret. Claude (`claude-haiku-5-5`) then re-checks every batch of headlines. It can veto false positives (≥70% confidence) and catch events the rules missed. Without the key the pipeline is rules-only.

## Data

| File | What |
|---|---|
| `data/catalog/*.json` | Hand-researched historical events, each with a source URL. Edit these to fix or add events. |
| `data/backtest.json` | Per event type × market: window stats, per-instrument stats, every underlying event. |
| `data/signals.json` | Live signals from the last 14 days, with score breakdown and price tracking. |
| `data/signal_history.json` | Compact long-term signal log (the live track record). |
| `data/pipeline.json` | Feed health, run history, unclassified sample. |
| `data/taxonomy.json` | Exported event types and rules (used by the in-browser classifier). |

## Method and caveats

See the **Method** page in the app. In short:
- Daily closes only.
- Small samples.
- Placebo (random-date) p-values and false-discovery-rate q-values.
- The earnings, FDA, recall and AI-launch catalogs are outcome-selected and flagged as such.
- On the blind held-out test split the classifier scores precision 0.91, recall 0.83 and F1 0.87, up from F1 0.50 before tuning on the dev split.

This is research tooling, not investment advice.
