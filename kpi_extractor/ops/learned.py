"""Learned rules on the extractor's side: code that reads what replay could not, earning its place against the AI.

Each rule kind is code (readers/anchor.py is the first). Its state comes with the company's state (symbol_state's
learnedRules): shadow (tried beside the AI: the AI's accepted reading decides, and whether the two agree is evidence),
live (reads before the AI; one filing in SPOT_CHECK_EVERY is still read by the AI, as a spot check) or demoted (not
used). Evidence goes back with the company's last store call, and the service moves the states
(public.kpi_record_rule_evidence: live after 30 agreements across 5 companies with none disagreeing; demoted on a
disagreement once live). A demoted rule's figures are re-read selectively: selector rule-<kind> (ops/reread.py)."""
from __future__ import annotations

import hashlib

SHADOW, LIVE, DEMOTED = "shadow", "live", "demoted"
SPOT_CHECK_EVERY = 10


def spot_checked(accession: str) -> bool:
    """Whether a live rule's filing is read by the AI as well: the same filings every run, about one in ten."""
    return int(hashlib.sha1(accession.encode()).hexdigest(), 16) % SPOT_CHECK_EVERY == 0


def same_figure(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9 * max(1.0, abs(left), abs(right))


def evidence(kind: str, mode: str, accession: str, key: str, rule_item, ai_item) -> dict:
    """One filing and KPI, the rule's figure beside the AI's accepted one."""
    locator = {name: rule_item.locator.get(name) for name in ("block", "quote", "value_text", "anchor") if rule_item.locator.get(name)}
    return {"kind": kind, "mode": mode, "accession": accession, "kpi": key, "ruleValue": float(rule_item.value),
            "aiValue": float(ai_item.value), "agreed": same_figure(float(rule_item.value), float(ai_item.value)),
            "ruleLocator": locator}
