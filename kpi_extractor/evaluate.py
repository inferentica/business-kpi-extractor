"""Scores the extractor against hand-verified values (evals/golden.json) without writing anything to production.

The run goes through the real pipeline and the real AI (through the control plane, as an "eval" run), but company
state starts empty and every value stays in memory. Each golden value is matched by company, period, unit and label.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from .cli import company_pool
from .control import ControlPlane
from .pipeline import SymbolPipeline, SymbolResult
from .sec import configure_identity

GOLDEN = Path("evals/golden.json")  # relative to the repository root, where the workflow runs


class EvalControl:
    """The control plane for AI calls and run bookkeeping; storage and company state stay local."""

    def __init__(self, plane: ControlPlane, stored_lists: bool = False):
        self.plane = plane
        self.stored_lists = stored_lists
        self.values: dict[str, list[dict]] = {}

    @property
    def run_id(self):
        return self.plane.run_id

    def call(self, operation: str, **payload) -> dict:
        if operation == "symbol_state":
            if self.stored_lists:
                # Production's KPI list and saved row choices, with every filing read fresh: the readers are scored
                # without paying for (or varying with) a newly written list.
                state = self.plane.call(operation, **payload)
                return {"spec": state.get("spec"), "curations": state.get("curations") or [], "filings": [], "values": []}
            return {"spec": None, "filings": [], "values": []}
        if operation == "store_statements":
            return {}
        if operation == "store":
            self.values.setdefault(payload["symbol"], []).extend(payload.get("values") or [])
            return {}
        return self.plane.call(operation, **payload)

    def ai(self, *args, **kwargs) -> str:
        return self.plane.ai(*args, **kwargs)


def evaluate_symbol(plane: ControlPlane, symbol: str, quarters: int) -> tuple[SymbolResult, list[dict]]:
    """One company in its own process; what it would have stored comes back with its result."""
    stored = os.environ.get("KPI_EVAL_LISTS", "fresh") == "stored"
    control = EvalControl(plane, stored_lists=stored)
    try:
        # With stored lists nothing is forced: the state is empty, so every filing is read, and the list is kept.
        result = SymbolPipeline(control, symbol, quarters, not stored, log=lambda line: print(line, flush=True)).run()
    except Exception as error:  # noqa: BLE001
        result = SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])
    return result, control.values.get(symbol, [])


def score(golden: list[dict], captured: dict[str, list[dict]]) -> dict:
    """Correct, wrong (found with another value) and missing, per golden value."""
    results = []
    for item in golden:
        pattern = re.compile(item["label"], re.I)
        target = date.fromisoformat(item["period_end"])
        latest: dict[tuple, dict] = {}
        for value in captured.get(item["symbol"], []):
            latest[(value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"])] = value
        candidates = [
            value for value in latest.values()
            if value["unit"] == item["unit"] and value["validation_status"] == "verified"
            and abs((date.fromisoformat(value["period_end"]) - target).days) <= 7
            and value["fiscal_period"] == item.get("fiscal_period", value["fiscal_period"])
            and pattern.search(value["kpi_label"])
        ]
        expected = float(item["value"])
        tolerance = float(item.get("tolerance", 1e-6))
        # The whole observation must match: the number, and its currency where the golden value names one.
        if any(abs(float(value["value"]) - expected) <= max(abs(expected) * tolerance, 1e-9)
               and value.get("currency") == item.get("currency", value.get("currency")) for value in candidates):
            outcome = "correct"
        elif candidates:
            outcome = "wrong"
        else:
            outcome = "optional" if item.get("optional") else "missing"
        results.append({**item, "outcome": outcome,
                        "found": [f"{float(value['value']):,.6g} {value.get('currency') or ''}".strip() for value in candidates][:3]})
    counted = [result for result in results if result["outcome"] != "optional"]
    correct = sum(1 for result in counted if result["outcome"] == "correct")
    wrong = sum(1 for result in counted if result["outcome"] == "wrong")
    return {
        "correct": correct,
        "wrong": wrong,
        "missing": sum(1 for result in counted if result["outcome"] == "missing"),
        "total": len(counted),
        "accuracy": correct / (correct + wrong) if correct + wrong else 1.0,
        "recall": correct / len(counted) if counted else 1.0,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default=str(GOLDEN))
    parser.add_argument("--quarters", type=int, default=6)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    configure_identity()
    golden = json.loads(Path(args.golden).read_text())
    symbols = sorted({item["symbol"] for item in golden})
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    started = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=args.quarters, forceRefresh=True)
    plane.run_id = started["runId"]
    with company_pool(max(1, min(args.workers, len(symbols)))) as pool:
        outcomes = list(pool.map(evaluate_symbol, [plane] * len(symbols), symbols, [args.quarters] * len(symbols)))
    results = [result for result, _values in outcomes]
    captured = {symbol: values for symbol, (_result, values) in zip(symbols, outcomes)}
    report = score(golden, captured)
    report["aiCalls"] = sum(result.ai_calls for result in results)
    report["flashOnlyReads"] = sum(result.flash_only_reads for result in results)
    report["proReads"] = sum(result.pro_reads for result in results)
    report["replayedReads"] = sum(result.replayed_reads for result in results)
    report["errors"] = {result.symbol: result.errors[:5] for result in results if result.errors}
    plane.call("finish", summary={key: value for key, value in report.items() if key != "results"})
    lines = [f"Golden set: {report['correct']}/{report['total']} correct, {report['wrong']} wrong, {report['missing']} missing "
             f"(accuracy {report['accuracy']:.1%}, recall {report['recall']:.1%}); AI calls {report['aiCalls']}",
             "", "| Company | Period | KPI | Expected | Found | Outcome |", "|---|---|---|---|---|---|"]
    for result in report["results"]:
        lines.append(f"| {result['symbol']} | {result['period_end']} | {result['label']} | {result['value']:,} | "
                     f"{', '.join(result['found']) or '—'} | {result['outcome']} |")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(text + "\n")
    Path("eval-results").mkdir(exist_ok=True)
    Path("eval-results/report.json").write_text(json.dumps({**report, "captured": captured}, default=str, indent=1))
    return 0 if report["wrong"] == 0 and report["missing"] == 0 else 1  # a required value not found fails the run


if __name__ == "__main__":
    sys.exit(main())
