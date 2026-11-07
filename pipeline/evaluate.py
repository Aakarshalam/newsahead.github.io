"""Classifier evaluation on held-out data.

  python3 -m pipeline.evaluate            # print metrics for gold dev/test and catalog headlines
"""
import collections
import json
import os

from . import classify, events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "tests", "data", "holdout_gold.json")


def predict(title):
    c = classify.classify_rules(dict(id="x", title=title, summary=""))
    return c["type"] if c else "none"


def metrics(pairs):
    """pairs: [(gold, pred)]. Micro precision/recall over event labels (none = negative)."""
    tp = sum(1 for g, p in pairs if g != "none" and p == g)
    fp = sum(1 for g, p in pairs if p != "none" and p != g)
    fn = sum(1 for g, p in pairs if g != "none" and p != g)
    P = tp / (tp + fp) if tp + fp else 1.0
    R = tp / (tp + fn) if tp + fn else 1.0
    F = 2 * P * R / (P + R) if P + R else 0.0
    return dict(n=len(pairs), events=sum(g != "none" for g, _ in pairs), tp=tp, fp=fp, fn=fn,
                precision=round(P, 3), recall=round(R, 3), f1=round(F, 3))


def gold_eval(split=None):
    with open(GOLD) as f:
        items = [x for x in json.load(f)["items"] if x["gold"] is not None and (split is None or x["split"] == split)]
    pairs = [(x["gold"], predict(x["title"])) for x in items]
    m = metrics(pairs)
    # event-level recall: distinct (label, cluster) events caught by at least one headline
    by_event = collections.defaultdict(list)
    for x, (g, p) in zip(items, pairs):
        if g != "none":
            by_event[g].append(p == g)
    m["types_present"] = {k: f"{sum(v)}/{len(v)}" for k, v in by_event.items()}
    m["errors"] = [dict(title=x["title"], gold=g, pred=p) for x, (g, p) in zip(items, pairs) if g != p]
    return m


def catalog_recall():
    """Recall on researched catalog headlines (descriptive, written independently of the rules)."""
    per = collections.defaultdict(lambda: [0, 0])
    for e in events.load_catalog():
        per[e["type"]][1] += 1
        per[e["type"]][0] += predict(e["headline"]) == e["type"]
    tot = sum(v[0] for v in per.values()), sum(v[1] for v in per.values())
    return dict(recall=round(tot[0] / tot[1], 3), hits=tot[0], n=tot[1],
                by_type={k: f"{v[0]}/{v[1]}" for k, v in sorted(per.items())})


def annotator_agreement():
    with open(GOLD) as f:
        items = json.load(f)["items"]
    n = len(items)
    po = sum(x["label_a"] == x["label_b"] for x in items) / n
    ca = collections.Counter(x["label_a"] for x in items)
    cb = collections.Counter(x["label_b"] for x in items)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n / n
    return dict(items=n, agreement=round(po, 3), cohen_kappa=round((po - pe) / (1 - pe), 3),
                disputed=sum(x["gold"] is None for x in items))


def report():
    out = {"gold_dev": gold_eval("dev"), "gold_test": gold_eval("test"), "catalog": catalog_recall(),
           "annotators": annotator_agreement(),
           "baseline_before_holdout_tuning": {"gold_test": {"precision": 0.923, "recall": 0.343, "f1": 0.5},
                                              "catalog": {"recall": 0.302}}}
    return out


if __name__ == "__main__":
    r = report()
    for k in ("gold_dev", "gold_test"):
        m = r[k]
        print(f"{k:<10} n={m['n']} events={m['events']} P={m['precision']} R={m['recall']} F1={m['f1']} by type {m['types_present']}")
    c = r["catalog"]
    print(f"catalog    recall={c['recall']} ({c['hits']}/{c['n']})")
