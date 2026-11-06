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
- Bonferroni-adjusted confidence.
- The earnings, FDA, recall and AI-launch catalogs are outcome-selected and flagged as such.
- The classifier rules were tuned on `tests/test_classify.py`, so live precision will be lower than the test numbers.

This is research tooling, not investment advice.
