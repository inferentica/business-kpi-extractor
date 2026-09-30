"""Runs one extraction: claims the run, processes each company, and reports the outcome back."""
from __future__ import annotations

import argparse
import multiprocessing
import os
import re
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor

from .control import ControlError, ControlPlane, DryRunControl
from .pipeline import SymbolPipeline, SymbolResult
from .sec import configure_identity

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Existing business_kpi_runs id (dispatched runs)")
    parser.add_argument("--symbols", default="", help="Comma-separated tickers; defaults to every company already tracked")
    parser.add_argument("--quarters", type=int, default=20, help="Quarters of history to keep (1-40)")
    parser.add_argument("--force", action="store_true", help="Re-read filings and re-propose KPI lists")
    parser.add_argument("--trigger", default="manual", choices=["scheduled", "manual", "watchlist_add", "backfill"])
    # Companies mostly wait on the AI, so more run at once than there are cores.
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="Local development: no AI, no writes")
    return parser.parse_args(argv)


def normalize_symbols(raw: str | list[str]) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else raw
    symbols = []
    for item in items:
        symbol = str(item).strip().upper()
        if symbol and _SYMBOL.match(symbol) and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def process_symbol(control, symbol: str, quarters: int, force: bool) -> SymbolResult:
    try:
        return SymbolPipeline(control, symbol, quarters, force, log=lambda line: print(line, flush=True)).run()
    except Exception as error:  # noqa: BLE001 - one company must not stop the run
        traceback.print_exc()
        return SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])


def company_pool(workers: int) -> ProcessPoolExecutor:
    """One process per company at a time, so parsing filings uses every core. SEC allows ten requests a second per
    machine; each process gets its share (edgartools reads the limit when a process starts)."""
    os.environ["EDGAR_RATE_LIMIT_PER_SEC"] = str(max(1, 9 // workers))
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                               initializer=configure_identity)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_identity()
    control = DryRunControl() if args.dry_run else ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    try:
        symbols = normalize_symbols(args.symbols)
        started = control.call("start", runId=args.run_id or None, triggerType=args.trigger, symbols=symbols,
                               quarters=max(1, min(args.quarters, 40)), forceRefresh=args.force)
        control.run_id = started["runId"]
        symbols = normalize_symbols(started.get("symbols") or [])
        quarters, force = int(started.get("quarters") or args.quarters), bool(started.get("forceRefresh"))
        print(f"Run {control.run_id}: {len(symbols)} companies, {quarters} quarters{' (forced)' if force else ''}")

        workers = max(1, min(args.workers, len(symbols) or 1))
        with company_pool(workers) as pool:
            results = list(pool.map(process_symbol, [control] * len(symbols), symbols,
                                    [quarters] * len(symbols), [force] * len(symbols)))
        summary = {
            "companies": len(results),
            "filings": sum(result.filings for result in results),
            "values": sum(result.values for result in results),
            "needsReview": sum(result.needs_review for result in results),
            "aiCalls": sum(result.ai_calls for result in results),
            "flashOnlyReads": sum(result.flash_only_reads for result in results),
            "proReads": sum(result.pro_reads for result in results),
            "replayedReads": sum(result.replayed_reads for result in results),
            "q4Gaps": {result.symbol: result.q4_gaps[:10] for result in results if result.q4_gaps},
            # Companies whose earnings-document values are mostly unverified: a list or reading that needs attention.
            "lowCoverage": {result.symbol: f"{result.release_verified}/{result.release_values}" for result in results
                            if result.release_values >= 5 and result.release_verified < 0.5 * result.release_values},
            "failedCompanies": [result.symbol for result in results if result.errors and not result.filings],
            "errors": {result.symbol: result.errors[:5] for result in results if result.errors},
        }
        print(f"Summary: {summary}")
        control.call("finish", summary=summary)
        return 0
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        if control.run_id or args.run_id:
            try:
                control.call("fail", errorMessage=f"{type(error).__name__}: {error}"[:500],
                             **({} if control.run_id else {"runId": args.run_id}))
            except ControlError:
                traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
