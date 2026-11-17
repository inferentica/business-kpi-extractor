"""KPI lists alone: Propose drafts each company's list as for a new company, and the draft is scored, with no filing
read beyond the XBRL reports Propose is shown and the latest quarter's documents it reads. Writes nothing to production.

Two sets are scored apart (evals/list_score.py, one to one):
- tuning: the companies whose lists the owner edited, against those lists and what each company still reports;
- held out (evals/heldout_lists.json): companies never used for tuning, against lists reviewed once and frozen with
  the release they were checked against.

    python -m kpi_extractor.evals.lists_eval                       # the golden set's companies and the held-out set
    python -m kpi_extractor.evals.lists_eval --symbols LLY,HD      # these companies' drafts too (to review for the set)

About ¥0.02 a company at Flash's logged rates, against about ¥0.15 for the same company in the full weekly run."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..ops.cli import company_pool, normalize_symbols
from ..ops.control import ControlPlane
from ..pipeline import SymbolPipeline, SymbolResult
from ..sources.sec import configure_identity
from .evaluate import GOLDEN, EvalControl
from .list_score import REMOVED, proposed_again, score_lists

HELDOUT = Path("evals/heldout_lists.json")  # {symbol: {"groups": [...], "accession": ..., "reviewed": ...}}


def draft_list(plane: ControlPlane, symbol: str, quarters: int) -> tuple[SymbolResult, dict | None, list[dict]]:
    """One company's fresh list, and what the run read on the way (its XBRL breakdowns)."""
    control = EvalControl(plane)
    try:
        result = SymbolPipeline(control, symbol, quarters, True, log=lambda line: print(line, flush=True), lists_only=True,
                                draft_audit=True).run()
    except Exception as error:  # noqa: BLE001
        import traceback
        print(f"{symbol}: {traceback.format_exc()}", flush=True)  # the cause, not only its message
        result = SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])
    return result, control.specs.get(symbol), control.values.get(symbol, [])


def _line(name: str, scored: dict) -> str:
    if not scored.get("companies"):
        return f"{name}: no companies"
    recall = f"{scored['recall']:.1%}" if scored.get("recall") is not None else "—"
    precision = f"{scored['precision']:.1%}" if scored.get("precision") is not None else "—"
    return (f"{name}: {scored['companies']} companies, recall {recall} of {scored['current']} lines still reported "
            f"({scored['named']} named, {scored['fromXbrl']} from XBRL; {scored['history']} history apart), "
            f"precision {precision} ({scored['kept']}/{scored['fresh']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="", help="Companies to draft besides the golden set's and the held-out set's")
    parser.add_argument("--quarters", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    configure_identity()
    heldout = json.loads(HELDOUT.read_text()) if HELDOUT.exists() else {}
    golden_symbols = {item["symbol"] for item in json.loads(GOLDEN.read_text())}
    extra = set(normalize_symbols(args.symbols)) if args.symbols else set()
    symbols = sorted(golden_symbols | set(heldout) | extra)
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    started = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=args.quarters, forceRefresh=True)
    plane.run_id = started["runId"]
    with company_pool(max(1, min(args.workers, len(symbols)))) as pool:
        outcomes = dict(zip(symbols, pool.map(draft_list, [plane] * len(symbols), symbols, [args.quarters] * len(symbols))))
    fresh = {symbol: spec for symbol, (_result, spec, _values) in outcomes.items() if spec}
    captured = {symbol: values for symbol, (_result, _spec, values) in outcomes.items()}
    owner = {}
    for symbol in sorted(golden_symbols & set(fresh)):
        state = plane.call("symbol_state", symbol=symbol)
        spec = state.get("spec") or {}
        if spec.get("owner_edited_at"):
            owner[symbol] = {**spec, "values": state.get("values") or []}
    tuning = score_lists(fresh, owner, captured)
    tuning["proposedAgain"] = proposed_again(fresh, owner, json.loads(REMOVED.read_text()) if REMOVED.exists() else [])
    held_lists = {symbol: {"groups": entry.get("groups") or []} for symbol, entry in heldout.items()}
    held = score_lists(fresh, held_lists, captured)
    # The same lists before the draft audit added to them (p4-draftaudit): what Propose alone listed.
    before = {result.symbol: {"groups": result.draft_before_audit} for result, _spec, _values in outcomes.values()
              if result.draft_before_audit is not None}
    before_audit = {"tuning": score_lists(before, owner, captured), "heldout": score_lists(before, held_lists, captured),
                    "added": sum(result.draft_audit_added for result, _spec, _values in outcomes.values())}
    results = [result for result, _spec, _values in outcomes.values()]
    report = {"tuning": tuning, "heldout": held, "beforeAudit": before_audit, "aiCalls": sum(result.ai_calls for result in results),
              "emptyLists": sorted(symbol for symbol in symbols if not any((fresh.get(symbol) or {}).get("groups") or [])),
              "errors": {result.symbol: result.errors[:3] for result in results if result.errors}, "freshLists": fresh}
    # What each fresh list left out, by source (Quotiva Review's Left out section; p4-leftout).
    report["leftOut"] = {source: sum(1 for spec in fresh.values() for line in spec.get("leftOut") or [] if line.get("source") == source)
                         for source in ("propose", "rule", "audit")}
    plane.call("finish", summary={key: value for key, value in report.items() if key not in ("freshLists", "beforeAudit")})
    lines = [_line("Tuning (owner-edited lists)", tuning), _line("Held out", held),
             _line("Tuning before the draft audit", before_audit["tuning"]),
             _line("Held out before the draft audit", before_audit["heldout"]),
             f"Draft audit added {before_audit['added']} KPIs",
             f"Removed groups proposed again: {len(tuning['proposedAgain'])}; empty lists: {', '.join(report['emptyLists']) or 'none'}; "
             f"AI calls {report['aiCalls']}",
             "Left out: " + ", ".join(f"{count} by {source}" for source, count in report["leftOut"].items()), "", "| Set | Company | Still reported | Named | From XBRL | Fresh | Recall | Precision |",
             "|---|---|---|---|---|---|---|---|"]
    for name, scored in (("tuning", tuning), ("held out", held)):
        for symbol, row in sorted(scored["byCompany"].items()):
            recall = f"{row['recall']:.0%}" if row["recall"] is not None else "—"
            precision = f"{row['precision']:.0%}" if row["precision"] is not None else "—"
            lines.append(f"| {name} | {symbol} | {row['current']} | {row['named']} | {row['fromXbrl']} | {row['fresh']} | {recall} | {precision} |")
    text = "\n".join(lines)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(summary).write_text(text + "\n")
    Path("eval-results").mkdir(exist_ok=True)
    Path("eval-results/lists.json").write_text(json.dumps(report, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
