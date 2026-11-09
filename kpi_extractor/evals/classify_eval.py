"""Scores the Classify rules against past classifications (evals/classify.json: 6-K filings the AI classified, with
what came of them), writing nothing and calling no AI. Prints how often the rules answer, any answer that disagrees
with the past one, and the titles of filings left to the AI, to tighten the rules by."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from ..rules.classify import classify_by_rules, titles
from ..sources.sec import classification_excerpt, company_profile, configure_identity, earnings_releases

CASES = Path("evals/classify.json")


def main() -> int:
    configure_identity()
    cases = json.loads(CASES.read_text())
    by_symbol: dict[str, dict[str, str]] = {}
    for case in cases:
        by_symbol.setdefault(case["symbol"], {})[case["accession"]] = case["label"]
    outcome: Counter = Counter()
    wrong, undecided = [], []
    for symbol, labels in by_symbol.items():
        profile, company = company_profile(symbol)
        refs = earnings_releases(company, profile, date(2015, 1, 1))
        for ref in refs:
            label = labels.get(ref.accession)
            if label is None:
                continue
            excerpt = classification_excerpt(ref)
            answer = classify_by_rules(excerpt)
            if answer is None:
                outcome["left to AI"] += 1
                undecided.append((symbol, ref.accession, label, titles(excerpt)[:300]))
            elif answer == label:
                outcome["right"] += 1
            else:
                outcome["wrong"] += 1
                wrong.append((symbol, ref.accession, label, answer, titles(excerpt)[:300]))
    found = sum(outcome.values())
    print(f"Classify rules on {found} of {len(cases)} past classifications: {dict(outcome)}")
    for item in wrong:
        print("WRONG", *item, sep=" | ")
    for item in undecided[:80]:
        print("AI", *item, sep=" | ")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
