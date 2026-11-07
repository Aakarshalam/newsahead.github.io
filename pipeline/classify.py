"""Headline → event type classification.

Layer 1 (always): deterministic regex rules from taxonomy.py.
Layer 2 (optional): if ANTHROPIC_API_KEY is set and the `anthropic` package is
installed, Claude re-classifies the batch of headlines with structured output.
Claude can confirm a rule match (boosting confidence), veto it, or catch an
event the rules missed. Without a key the pipeline is fully rule-based.
"""
import json
import os
import re

from . import taxonomy

CLAUDE_MODEL = os.environ.get("NEWSALPHA_CLAUDE_MODEL", "claude-haiku-5-5")
UNRESOLVED_PENALTY = 10
RESOLVABLE_LATER = {"fda_approval", "fda_trial_failure", "major_recall"}
MEGACAPS = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX"}
_compiled = {}


def _rx(p):
    if p not in _compiled:
        _compiled[p] = re.compile(p, re.I)
    return _compiled[p]


# Aliases that are also ordinary words: only match when capitalised in the text.
AMBIGUOUS = {"target", "meta", "apple", "amazon", "ford", "lilly", "stat", "kla", "gm", "real", "vertex", "abbott"}


def find_companies(text):
    found = []
    low = text.lower()
    for name, tkr in sorted(taxonomy.COMPANY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if name in AMBIGUOUS:
            hit = re.search(r"(?<![\w])(" + re.escape(name.capitalize()) + "|" + re.escape(name.upper()) + r")(?![\w])", text)
        else:
            hit = re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", low)
        if hit and tkr not in found:
            found.append(tkr)
    # explicit $TICKER or (NASDAQ: XYZ) mentions
    for m in re.finditer(r"\$([A-Z]{1,5})\b|\((?:NASDAQ|NYSE|Nasdaq|NYSE American)\s*:\s*([A-Z.]{1,6})\)", text):
        t = m.group(1) or m.group(2)
        if t not in found:
            found.append(t)
    return found


def find_currencies(text):
    low = text.lower()
    out = []
    for name, sym in sorted(taxonomy.CURRENCY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(name) + r"\b", low) and sym not in out:
            out.append(sym)
    return out


def rule_match(et, title, summary=""):
    """Score how well `title` matches event type `et`. Returns (score, hits) or (0, None)."""
    p = et["patterns"]
    hits = []
    for grp in p["all"]:
        m = _rx(grp).search(title)
        if not m:
            return 0, None
        hits.append(m.group(0))
    for v in p["none"]:
        if _rx(v).search(title):
            return 0, None
    boosts = sum(1 for b in p["boost"] if _rx(b).search(title + " " + summary))
    score = min(97, 62 + 6 * len(p["all"]) + 8 * boosts)
    return score, hits


def tickers_for(et, title, summary=""):
    if et["id"] == "currency_devaluation":
        return find_currencies(title)
    if not et["company_specific"]:
        return []
    cos = find_companies(title) or find_companies(summary)
    if et["id"].startswith("megacap_earnings"):
        cos = [c for c in cos if c in MEGACAPS]
    return cos


def classify_rules(article):
    title, summary = article["title"], article.get("summary", "")
    best = None
    for et in taxonomy.EVENT_TYPES:
        score, hits = rule_match(et, title, summary)
        if not score:
            continue
        tickers = tickers_for(et, title, summary)
        needs_tickers = et["company_specific"] and not et["default_tickers"]
        if et["id"].startswith("megacap_earnings") or et["id"] == "currency_devaluation":
            needs_tickers = True
        unresolved = False
        if needs_tickers and not tickers:
            if et["id"] not in RESOLVABLE_LATER:
                continue
            # e.g. "FDA approves amivantamab": the event is clear but the company is
            # only named by other outlets. The pipeline attaches it to that cluster.
            unresolved = True
        cand = dict(type=et["id"], match=score - (UNRESOLVED_PENALTY if unresolved else 0), hits=hits,
                    tickers=tickers or et["default_tickers"], method="rules", unresolved=unresolved)
        if best is None or cand["match"] > best["match"]:
            best = cand
    return best


# ── optional Claude layer ────────────────────────────────────────────────────
def claude_available():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _claude_prompt(batch):
    types = "\n".join(f"- {t['id']}: {t['label']} ({t['geo']})" for t in taxonomy.EVENT_TYPES)
    lines = "\n".join(f"{i}. {a['title']}" for i, a in enumerate(batch))
    return (
        "You label financial news headlines for an event-driven trading backtest.\n"
        "For each headline decide whether it reports that one of these events HAS JUST HAPPENED "
        "(announced, released, decided, launched). Previews, speculation, opinion, explainers, "
        "polls about odds, and follow-up analysis of older events are 'none'.\n\n"
        f"Event types:\n{types}\n\n"
        "For company-specific types give the affected companies' US stock tickers (Yahoo symbols); "
        "for currency_devaluation give the Yahoo USD/xxx symbol like 'TRY=X'. Otherwise an empty list.\n"
        "confidence is 0-100 that the label is right.\n\n"
        f"Headlines:\n{lines}"
    )


def classify_claude(articles, log=print, batch_size=40):
    """Return {article_id: {type, confidence, tickers}} from Claude, or {} on any failure."""
    import anthropic
    client = anthropic.Anthropic()
    type_ids = [t["id"] for t in taxonomy.EVENT_TYPES] + ["none"]
    schema = {
        "type": "object",
        "properties": {"labels": {"type": "array", "items": {
            "type": "object",
            "properties": {"i": {"type": "integer"}, "type": {"type": "string", "enum": type_ids},
                           "confidence": {"type": "integer"},
                           "tickers": {"type": "array", "items": {"type": "string"}}},
            "required": ["i", "type", "confidence", "tickers"], "additionalProperties": False}}},
        "required": ["labels"], "additionalProperties": False,
    }
    out = {}
    for start in range(0, len(articles), batch_size):
        batch = articles[start:start + batch_size]
        try:
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=8000,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": _claude_prompt(batch)}],
            )
            if resp.stop_reason not in ("end_turn", "stop_sequence"):
                log(f"  claude: batch {start} stopped with {resp.stop_reason}; skipping")
                continue
            text = next(b.text for b in resp.content if b.type == "text")
            for lab in json.loads(text)["labels"]:
                if 0 <= lab["i"] < len(batch):
                    out[batch[lab["i"]]["id"]] = lab
        except anthropic.RateLimitError as e:
            log(f"  claude: rate limited ({e}); stopping Claude layer for this run")
            break
        except anthropic.APIStatusError as e:
            log(f"  claude: API error {e.status_code}: {e.message}; skipping batch")
        except anthropic.APIConnectionError as e:
            log(f"  claude: connection error {e}; skipping batch")
        except (StopIteration, json.JSONDecodeError, KeyError) as e:
            log(f"  claude: unparseable response ({e}); skipping batch")
    return out


def merge(rule, llm):
    """Combine rule and Claude verdicts into a final classification (or None)."""
    if llm is None:
        return rule
    if llm["type"] == "none":
        # Claude vetoes a rule hit only when confident.
        if rule and llm["confidence"] >= 70:
            return None
        return rule
    et = taxonomy.TYPES_BY_ID[llm["type"]]
    tickers = [t.upper() for t in llm.get("tickers") or []] or (rule or {}).get("tickers") or et["default_tickers"]
    if rule and rule["type"] == llm["type"]:
        return dict(rule, match=min(99, max(rule["match"], llm["confidence"]) + 5), method="rules+claude",
                    tickers=rule["tickers"] or tickers)
    if llm["confidence"] < 70:
        return rule
    if et["company_specific"] and not tickers:
        return rule
    return dict(type=llm["type"], match=min(90, llm["confidence"]), hits=[], tickers=tickers, method="claude")


def classify_all(articles, use_claude=None, log=print):
    rules = {a["id"]: classify_rules(a) for a in articles}
    if use_claude is None:
        use_claude = claude_available()
    llm = {}
    if use_claude:
        log(f"  claude: classifying {len(articles)} headlines with {CLAUDE_MODEL}")
        llm = classify_claude(articles, log=log)
    out = {}
    for a in articles:
        res = merge(rules[a["id"]], llm.get(a["id"])) if use_claude else rules[a["id"]]
        if res:
            out[a["id"]] = res
    return out, bool(use_claude and llm)
