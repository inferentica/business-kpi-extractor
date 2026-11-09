"""Runs one extraction: claims the run, processes each company, and reports the outcome back."""
from __future__ import annotations

import argparse
import dataclasses
import multiprocessing
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from .control import ControlError, ControlPlane, DryRunControl
from .memory import MemoryWatch, peak_gb
from .offpeak import seconds_until_offpeak
from ..pipeline import SymbolPipeline, SymbolResult
from ..sources.sec import _temporary, configure_identity

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Existing business_kpi_runs id (dispatched runs)")
    parser.add_argument("--symbols", default="", help="Comma-separated tickers; defaults to every company already tracked")
    parser.add_argument("--quarters", type=int, default=20, help="Quarters of history to keep (1-40)")
    parser.add_argument("--force", action="store_true", help="Re-read filings and re-propose KPI lists")
    parser.add_argument("--retrace", action="store_true",
                        help="Re-read only filings holding AI figures read before calls were traced; lists stay as they are")
    parser.add_argument("--trigger", default="manual", choices=["scheduled", "manual", "watchlist_add", "backfill"])
    # Companies mostly wait on the AI, but a re-read of every report parses ten years of XBRL at up to 2 GB a company:
    # six at once took the 16 GB runner down (the 22-company runs after reader bumps), so four.
    parser.add_argument("--workers", type=int, default=4)
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


# About ten years of 10-Q and 10-K segments: read from XBRL by code, so the longer window costs downloads, not AI.
REPORT_QUARTERS = 40


# A company whose run fails on a passing SEC failure (a read timeout, a busy server) is run again once after this wait:
# Prudential dropped out of a trial on one ReadTimeout that no single fetch's retries caught.
COMPANY_RETRY_WAIT = 60


def run_symbol(symbol: str, run, log=print) -> SymbolResult:
    """`run()` for one company, once more after a temporary failure; any other failure is the company's result, never the run's."""
    for attempt in (1, 2):
        try:
            return run()
        except Exception as error:  # noqa: BLE001 - one company must not stop the run
            traceback.print_exc()
            if attempt == 1 and _temporary(error):
                log(f"{symbol}: {type(error).__name__} from SEC; running the company again in {COMPANY_RETRY_WAIT} s")
                time.sleep(COMPANY_RETRY_WAIT)
                continue
            return SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])
    return SymbolResult(symbol, errors=["unreachable"])


def process_symbol(control, symbol: str, quarters: int, force: bool, wait_for_lock: bool = False,
                   retrace: bool = False) -> SymbolResult:
    try:
        return run_symbol(symbol, lambda: SymbolPipeline(control, symbol, quarters, force, log=lambda line: print(line, flush=True),
                                                         wait_for_lock=wait_for_lock, report_quarters=REPORT_QUARTERS,
                                                         retrace=retrace).run())
    finally:
        print(f"{symbol}: this process has held at most {peak_gb():.1f} GB", flush=True)


def _tally(counts) -> dict[str, int]:
    total: dict[str, int] = {}
    for count in counts:
        for key, number in count.items():
            total[key] = total.get(key, 0) + number
    return dict(sorted(total.items()))


def company_pool(workers: int) -> ProcessPoolExecutor:
    """One process per company at a time, so parsing filings uses every core. SEC allows ten requests a second per
    machine; each process gets its share (edgartools reads the limit when a process starts)."""
    os.environ["EDGAR_RATE_LIMIT_PER_SEC"] = str(max(1, 9 // workers))
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                               initializer=configure_identity)


# Every minute the worker tells the control plane it is alive; ten minutes of silence and its companies go to a new one.
RENEW_SECONDS = 60


def wait_for_offpeak(control, log=print, sleep=time.sleep, clock=lambda: datetime.now(timezone.utc)) -> None:
    """A run that can wait for DeepSeek's half-price hours waits here, after taking its run, renewing it every minute so
    the watcher sees a live worker. Waiting before taking it, a Refresh pressed early in a peak (up to four hours) was
    given up as never started every 20 minutes until its attempts ran out (NVDA and AVGO, 2026-11-09)."""
    remaining = seconds_until_offpeak(clock())
    if not remaining:
        return
    log(f"DeepSeek peak hours: waiting {timedelta(seconds=remaining)} for off-peak prices")
    while (remaining := seconds_until_offpeak(clock())) > 0:
        sleep(min(RENEW_SECONDS, remaining))
        try:
            control.call("renew", attempts=2)
        except ControlError as error:
            log(f"Could not renew the run while waiting: {str(error)[:200]}")


def company_result(result: SymbolResult, lowest_available_gb: float | None = None) -> dict:
    """What the control plane keeps of a company's reading, for the run's summary."""
    data = dataclasses.asdict(result)
    data["errors"] = [str(error)[:300] for error in data["errors"][:5]]
    data["q4_gaps"] = data["q4_gaps"][:10]
    data["lowest_available_gb"] = round(lowest_available_gb, 2) if lowest_available_gb is not None else None
    return data


def work_queue(control, pool, workers: int, quarters: int, force: bool, waits: bool, memory=None, log=print,
               retrace: bool = False) -> str | None:
    """Takes the run's companies from its queue a few at a time and reports each as it finishes. If this worker dies,
    its companies go back to the queue and a new worker carries on; the run finishes when its last company does.
    Returns how the run finished, when this worker finished it."""
    in_flight: dict = {}
    finished = None
    claiming = True
    stop = threading.Event()

    def renew():
        while not stop.wait(RENEW_SECONDS):
            try:
                control.call("renew", attempts=2)
            except ControlError as error:
                log(f"Could not renew this worker's companies: {str(error)[:200]}")

    renewing = threading.Thread(target=renew, daemon=True)
    renewing.start()
    try:
        while True:
            while claiming and len(in_flight) < workers:
                reply = control.call("claim")
                symbol = reply.get("symbol")
                if not symbol:
                    claiming = False
                    finished = reply.get("finished") or finished
                    break
                log(f"{symbol}: claimed")
                in_flight[pool.submit(process_symbol, control, symbol, quarters, force, waits, retrace)] = symbol
            if not in_flight:
                return finished
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                symbol = in_flight.pop(future)
                result = future.result()  # a broken pool raises: the worker is lost and its companies requeued
                lowest = getattr(memory, "lowest_available_gb", None)
                reply = control.call("complete", symbol=symbol, result=company_result(result, lowest))
                if not reply.get("accepted"):
                    log(f"{symbol}: no longer this worker's; its result was not kept")
                finished = reply.get("finished") or finished
    finally:
        stop.set()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_identity()
    control = DryRunControl() if args.dry_run else ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    try:
        symbols = normalize_symbols(args.symbols)
        started = control.call("start", runId=args.run_id or None, triggerType=args.trigger, symbols=symbols,
                               quarters=max(1, min(args.quarters, 40)), forceRefresh=args.force,
                               queued=not args.dry_run, retrace=args.retrace)
        control.run_id = started["runId"]
        symbols = normalize_symbols(started.get("symbols") or [])
        quarters, force = int(started.get("quarters") or args.quarters), bool(started.get("forceRefresh"))
        print(f"Run {control.run_id}: {len(symbols)} companies, {quarters} quarters{' (forced)' if force else ''}")

        workers = max(1, min(args.workers, len(symbols) or 1))
        # A Refresh (or a newly watched company) waits for a run already on its company; scheduled runs move on.
        waits = args.trigger in ("manual", "watchlist_add")
        if started.get("queued"):
            if args.trigger == "manual":
                wait_for_offpeak(control, log=lambda line: print(line, flush=True))
            with company_pool(workers) as pool, MemoryWatch(pool, log=lambda line: print(line, flush=True)) as memory:
                # Kept on the run, so a worker that carries on after a lost one retraces too.
                finished = work_queue(control, pool, workers, quarters, force, waits, memory,
                                      log=lambda line: print(line, flush=True), retrace=bool(started.get("retrace")))
            print(f"Run {control.run_id}: {'finished, ' + finished if finished else 'this worker is done'}")
            return 0
        with company_pool(workers) as pool:
            with MemoryWatch(pool, log=lambda line: print(line, flush=True)) as memory:
                results = list(pool.map(process_symbol, [control] * len(symbols), symbols,
                                        [quarters] * len(symbols), [force] * len(symbols), [waits] * len(symbols)))
        summary = {
            "companies": len(results),
            "filings": sum(result.filings for result in results),
            "values": sum(result.values for result in results),
            "needsReview": sum(result.needs_review for result in results),
            "aiCalls": sum(result.ai_calls for result in results),
            "flashOnlyReads": sum(result.flash_only_reads for result in results),
            "secondReads": sum(result.second_reads for result in results),
            "secondReadReasons": _tally(result.second_read_reasons for result in results),
            "replayedReads": sum(result.replayed_reads for result in results),
            "q4Gaps": {result.symbol: result.q4_gaps[:10] for result in results if result.q4_gaps},
            # Companies whose earnings-document values are mostly unverified: a list or reading that needs attention.
            "lowCoverage": {result.symbol: f"{result.release_verified}/{result.release_values}" for result in results
                            if result.release_values >= 5 and result.release_verified < 0.5 * result.release_values},
            "failedCompanies": [result.symbol for result in results if result.errors and not result.filings],
            "skippedCompanies": [result.symbol for result in results if result.skipped],
            "trimmedKpis": {result.symbol: result.trimmed_kpis for result in results if result.trimmed_kpis},
            "errors": {result.symbol: result.errors[:5] for result in results if result.errors},
            "servedChanges": {result.symbol: result.served_changes for result in results if result.served_changes},
            "lowestAvailableMemoryGb": round(memory.lowest_available_gb, 1) if memory.lowest_available_gb is not None else None,
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
