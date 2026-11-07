"""Regression guard on the blind, two-annotator gold set (tests/data/holdout_gold.json).

Rules were tuned on split=dev only. These thresholds sit a little below the
frozen split=test result (P 0.906, R 0.829, F1 0.866 on 2026-11-06), so a rule
change that degrades generalisation fails CI.
"""
import json
import os
import unittest

from pipeline import evaluate, taxonomy


class TestHoldout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(evaluate.GOLD) as f:
            cls.data = json.load(f)

    def test_gold_file_integrity(self):
        items = self.data["items"]
        valid = {t["id"] for t in taxonomy.EVENT_TYPES} | {"none"}
        self.assertGreaterEqual(len(items), 400)
        for x in items:
            self.assertIn(x["label_a"], valid)
            self.assertIn(x["label_b"], valid)
            self.assertIn(x["split"], ("dev", "test"))
            self.assertEqual(x["gold"], x["label_a"] if x["label_a"] == x["label_b"] else None)

    def test_annotator_agreement(self):
        items = self.data["items"]
        agree = sum(x["label_a"] == x["label_b"] for x in items) / len(items)
        self.assertGreater(agree, 0.95, "gold labels are only meaningful if independent annotators agree")

    def test_heldout_test_split(self):
        m = evaluate.gold_eval("test")
        print(f"\nheld-out test: P={m['precision']} R={m['recall']} F1={m['f1']} (n={m['n']}, events={m['events']})")
        self.assertGreaterEqual(m["precision"], 0.85)
        self.assertGreaterEqual(m["recall"], 0.75)
        self.assertGreaterEqual(m["f1"], 0.80)

    def test_catalog_headline_recall_floor(self):
        c = evaluate.catalog_recall()
        print(f"\ncatalog headline recall: {c['recall']} ({c['hits']}/{c['n']})")
        self.assertGreaterEqual(c["recall"], 0.35)


if __name__ == "__main__":
    unittest.main()
