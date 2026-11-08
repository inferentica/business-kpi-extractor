"""Reads companies the extractor has never seen, end to end, and writes nothing to production: the Phase 2 test.

Each company runs as a new one would (a fresh KPI list, every filing of the window read), through the real pipeline and
the real AI (an "eval" run on the control plane, so every call is logged and costed). What it would have stored stays
in memory and is scored against the gate: how much is accepted without a person, why the rest is held, how many
review items each company-quarter brings, what it costs, and how often a figure is only outside SEC filings. A random
sample of the accepted figures is written out for a hand audit.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

from .ai import MISSING_REASONS
from .cli import company_pool, normalize_symbols, run_symbol
from .control import ControlPlane
from .evaluate import EvalControl
from .pipeline import SymbolPipeline, SymbolResult
from .sec import configure_identity
from .steps.shared import AI_GROUP_PREFIX, value_key

AUDIT_SAMPLE = 60  # accepted figures drawn for the hand audit
_MISSING = re.compile(r"^missing reasons: (.*)$")
# A note cut at 300 characters can end in half a code ("unsta"), so only whole codes count.
_CODES = frozenset((*MISSING_REASONS, "unstated", "left_out_names_it"))
# Notes a held figure carries that are not why it is held.
_NOT_A_REASON = {"confirmed by review", "settled by the checks"}
_SECOND = re.compile(r"^second read: (\w+)")


class TrialControl(EvalControl):
    """As Evaluate's control (company state starts empty, nothing reaches the store), also keeping filings and lists."""

    def __init__(self, plane: ControlPlane):
        super().__init__(plane, stored_lists=False)
        self.final: dict[str, dict[tuple, dict]] = {}
        self.filings: dict[str, dict[str, dict]] = {}

    def call(self, operation: str, **payload) -> dict:
        if operation == "store":
            symbol = payload["symbol"]
            for value in payload.get("values") or []:
                self.final.setdefault(symbol, {})[value_key(value)] = dict(value)  # a later write of a key replaces it
            for filing in payload.get("filings") or []:
                self.filings.setdefault(symbol, {})[filing["accession"]] = dict(filing)
        return super().call(operation, **payload)


def trial_symbol(plane: ControlPlane, symbol: str, quarters: int) -> tuple[SymbolResult, list[dict], list[dict], dict | None]:
    control = TrialControl(plane)
    result = run_symbol(symbol, lambda: SymbolPipeline(control, symbol, quarters, True, log=lambda line: print(line, flush=True)).run())
    return (result, list(control.final.get(symbol, {}).values()), list(control.filings.get(symbol, {}).values()),
            control.specs.get(symbol))


def _reason(note: str) -> str:
    """A review note without its figures: "changed 1.9x in a quarter" and "changed 0.4x in a quarter" are one reason."""
    return re.sub(r"[-+]?\$?\d[\d,.]*(?:x|%| points| billion| million)?", "#", note)[:90]


def company_report(symbol: str, result: SymbolResult, values: list[dict], filings: list[dict], spec: dict | None) -> dict:
    ai = [v for v in values if v["group_key"].startswith(AI_GROUP_PREFIX)]
    other = [v for v in values if not v["group_key"].startswith(AI_GROUP_PREFIX)]
    status = Counter(v["validation_status"] for v in ai)
    held = [v for v in ai if v["validation_status"] == "needs_review"]
    quarters = {(v["fiscal_year"], v["fiscal_period"]) for v in ai if v["validation_status"] != "rejected"}
    held_quarters = {(v["fiscal_year"], v["fiscal_period"]) for v in held}
    missing, second = Counter(), Counter()
    for filing in filings:
        for note in filing.get("notes") or []:
            if match := _MISSING.match(note):
                for pair in match.group(1).split(", "):
                    code = pair.rsplit("=", 1)[-1] if "=" in pair else ""
                    if code in _CODES:
                        missing[code] += 1
            if match := _SECOND.match(note):
                second[match.group(1)] += 1
    accepted = status["verified"] + status["needs_review"]
    return {
        "symbol": symbol,
        "kpis": sum(len(group.get("kpis") or []) for group in (spec or {}).get("groups") or []),
        "groups": [group.get("label") for group in (spec or {}).get("groups") or []],
        "filings": Counter(filing.get("status") for filing in filings),
        "aiFigures": dict(status),
        "xbrlFigures": sum(1 for v in other if v["validation_status"] == "verified"),
        "autoAccepted": status["verified"] / accepted if accepted else None,
        "quarters": len(quarters),
        "reviewItems": len(held),
        "reviewItemsPerQuarter": len(held) / len(quarters) if quarters else None,
        "quartersWithReview": len(held_quarters),
        "reviewReasons": Counter(_reason(note) for v in held for note in v.get("notes") or [] if note and note not in _NOT_A_REASON),
        "missingReasons": dict(missing),
        "secondReadReasons": dict(result.second_read_reasons or second),
        "aiCalls": result.ai_calls,
        # How each release was read: by code alone (the way it was read before), by one AI reading, or by two.
        "reads": {"code": result.replayed_reads, "oneAi": result.flash_only_reads, "twoAi": result.second_reads},
        "errors": result.errors[:5],
    }


def audit_sample(values_by_symbol: dict[str, list[dict]], size: int, seed: int) -> list[dict]:
    """Accepted AI figures drawn at random, spread over companies, with what a person needs to check each one."""
    pool = [(symbol, v) for symbol, values in sorted(values_by_symbol.items()) for v in values
            if v["group_key"].startswith(AI_GROUP_PREFIX) and v["validation_status"] == "verified" and v.get("method") == "ai"]
    rng = random.Random(seed)
    rng.shuffle(pool)
    per_company: Counter = Counter()
    sample = []
    cap = max(2, size // max(1, len(values_by_symbol)) + 1)
    for symbol, v in pool:
        if per_company[symbol] >= cap:
            continue
        per_company[symbol] += 1
        locator = v.get("locator") or {}
        sample.append({"symbol": symbol, "kpi": f"{v.get('group_label')} / {v.get('kpi_label')}", "period": f"{v['fiscal_period']} {v['fiscal_year']}",
                       "periodEnd": v.get("period_end"), "value": v["value"], "unit": v.get("unit"), "currency": v.get("currency"),
                       "source": v.get("source_url"), "where": locator.get("quote") or f"{locator.get('table')} row {locator.get('row_label')!r}",
                       "verdict": "", "note": ""})
        if len(sample) >= size:
            break
    return sample


def summarize(companies: list[dict]) -> dict:
    status = Counter()
    for company in companies:
        status.update(company["aiFigures"])
    accepted = status["verified"] + status["needs_review"]
    quarters = sum(company["quarters"] for company in companies)
    missing, reasons, second, reads = Counter(), Counter(), Counter(), Counter()
    for company in companies:
        reads.update(company.get("reads") or {})
        missing.update(company["missingReasons"])
        reasons.update(company["reviewReasons"])
        second.update(company["secondReadReasons"])
    return {
        "companies": len(companies),
        "withFigures": sum(1 for company in companies if company["quarters"]),
        "failed": [company["symbol"] for company in companies if company["errors"] and not company["quarters"]],
        "aiFigures": dict(status),
        "autoAccepted": status["verified"] / accepted if accepted else None,
        "reviewItemsPerQuarter": status["needs_review"] / quarters if quarters else None,
        "companyQuarters": quarters,
        "aiCalls": sum(company["aiCalls"] for company in companies),
        "topReviewReasons": reasons.most_common(12),
        "missingReasons": dict(missing),
        "secondReadReasons": dict(second),
        "reads": dict(reads),
        # The learning measures: the share of releases read by code alone, and AI calls per company and per quarter.
        "codeShare": reads["code"] / sum(reads.values()) if sum(reads.values()) else None,
        "aiCallsPerCompany": sum(company["aiCalls"] for company in companies) / len(companies) if companies else None,
        "aiCallsPerQuarter": sum(company["aiCalls"] for company in companies) / quarters if quarters else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True, help="Comma-separated tickers")
    parser.add_argument("--quarters", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    configure_identity()
    symbols = normalize_symbols(args.symbols)
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    started = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=args.quarters, forceRefresh=True)
    plane.run_id = started["runId"]
    with company_pool(max(1, min(args.workers, len(symbols)))) as pool:
        outcomes = list(pool.map(trial_symbol, [plane] * len(symbols), symbols, [args.quarters] * len(symbols)))
    companies = [company_report(symbol, *outcome) for symbol, outcome in zip(symbols, outcomes)]
    summary = summarize(companies)
    sample = audit_sample({symbol: outcome[1] for symbol, outcome in zip(symbols, outcomes)}, AUDIT_SAMPLE, args.seed)
    plane.call("finish", summary={"trial": summary, "companies": [{key: company[key] for key in
                                  ("symbol", "kpis", "aiFigures", "autoAccepted", "reviewItemsPerQuarter", "aiCalls", "errors")}
                                  for company in companies]})
    pct = lambda share: f"{share:.0%}" if share is not None else "—"  # noqa: E731
    lines = [f"Trial: {summary['companies']} companies, {summary['withFigures']} with figures, {summary['companyQuarters']} company-quarters; "
             f"auto-accepted {pct(summary['autoAccepted'])}; review items per company-quarter "
             f"{summary['reviewItemsPerQuarter']:.2f}; AI calls {summary['aiCalls']}" if summary["reviewItemsPerQuarter"] is not None
             else f"Trial: {summary['companies']} companies, no figures",
             f"Reads: {summary['reads'].get('code', 0)} by code, {summary['reads'].get('oneAi', 0)} by one AI reading, "
             f"{summary['reads'].get('twoAi', 0)} by two (code share {pct(summary['codeShare'])}); AI calls per company "
             f"{summary['aiCallsPerCompany'] or 0:.1f}, per company-quarter {summary['aiCallsPerQuarter'] or 0:.2f}",
             f"Missing reasons: {summary['missingReasons']}", f"Second reads: {summary['secondReadReasons']}",
             f"Failed companies: {summary['failed']}", "", "| Company | KPIs | Accepted | Held | Auto | Items/quarter | AI calls | Code / 1 AI / 2 AI | Errors |",
             "|---|---|---|---|---|---|---|---|---|"]
    for company in companies:
        lines.append(f"| {company['symbol']} | {company['kpis']} | {company['aiFigures'].get('verified', 0)} | "
                     f"{company['aiFigures'].get('needs_review', 0)} | {pct(company['autoAccepted'])} | "
                     f"{company['reviewItemsPerQuarter'] if company['reviewItemsPerQuarter'] is None else round(company['reviewItemsPerQuarter'], 2)} | "
                     f"{company['aiCalls']} | {company['reads']['code']} / {company['reads']['oneAi']} / {company['reads']['twoAi']} | "
                     f"{'; '.join(company['errors'])[:80]} |")
    lines += ["", "Top review reasons:"] + [f"- {count} × {reason}" for reason, count in summary["topReviewReasons"]]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(text + "\n")
    out = Path("eval-results")
    out.mkdir(exist_ok=True)
    (out / "trial.json").write_text(json.dumps({"summary": summary, "companies": companies,
                                                "values": {s: o[1] for s, o in zip(symbols, outcomes)},
                                                "lists": {s: o[3] for s, o in zip(symbols, outcomes)},
                                                "filings": {s: o[2] for s, o in zip(symbols, outcomes)}}, default=str, indent=1))
    (out / "audit_sample.json").write_text(json.dumps(sample, default=str, indent=1))
    (out / "patterns.json").write_text(json.dumps(reading_patterns({s: o[1] for s, o in zip(symbols, outcomes)}),
                                                  default=str, indent=1))
    return 0


def reading_patterns(values_by_symbol: dict[str, list[dict]]) -> list[dict]:
    """What each accepted AI reading proves, kept for the learning loop: the KPI, the row label it was found under, its
    column's heading, the scale and currency wording, and how it was located. Only verified figures count: a held
    reading proves nothing yet. Records only; nothing reads them back in Phase 2."""
    patterns = []
    for symbol, values in sorted(values_by_symbol.items()):
        for v in values:
            if not v["group_key"].startswith(AI_GROUP_PREFIX) or v["validation_status"] != "verified" or v.get("method") != "ai":
                continue
            locator = v.get("locator") or {}
            patterns.append({
                "symbol": symbol, "group": v.get("group_label"), "kind": v.get("group_kind"), "kpi": v.get("kpi_label"),
                "unit": v.get("unit"), "currency": v.get("currency"), "period_end": v.get("period_end"),
                "how": "tag" if locator.get("tagged") else "quote" if locator.get("quote") else "table" if locator.get("table") else "other",
                "row_label": locator.get("row_label"), "header": locator.get("header"), "context": locator.get("context"),
                "scale": locator.get("scale"), "scale_text": locator.get("scale_text"), "concept": locator.get("concept"),
                "quote": locator.get("quote"),
            })
    return patterns


if __name__ == "__main__":
    sys.exit(main())
