"""A worker takes a run's companies from the queue, reports each as it finishes, and keeps its claim alive; a broken
worker stops without reporting what it did not finish, so those companies go to the next worker."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from kpi_extractor.ops import cli
from kpi_extractor.pipeline import SymbolResult


class Queue:
    """The control plane's queue for one run: claims in order, completions, and the run's finish."""

    def __init__(self, symbols):
        self.waiting = list(symbols)
        self.working: set[str] = set()
        self.done: dict[str, dict] = {}
        self.calls: list[str] = []

    def call(self, operation, **payload):
        self.calls.append(operation)
        if operation == "claim":
            if self.waiting:
                symbol = self.waiting.pop(0)
                self.working.add(symbol)
                return {"symbol": symbol}
            return {"symbol": None, **self._settle()}
        if operation == "complete":
            accepted = payload["symbol"] in self.working
            self.working.discard(payload["symbol"])
            if accepted:
                self.done[payload["symbol"]] = payload["result"]
            return {"accepted": accepted, **self._settle()}
        if operation == "renew":
            return {"renewed": len(self.working)}
        raise AssertionError(operation)

    def _settle(self):
        return {"finished": "succeeded"} if not self.waiting and not self.working and self.done else {}


def test_a_worker_reads_every_queued_company_and_the_last_one_finishes_the_run(monkeypatch):
    monkeypatch.setattr(cli, "process_symbol", lambda control, symbol, *_: SymbolResult(symbol, filings=2, ai_calls=7))
    queue = Queue(["TSM", "ASML", "AAPL", "MSFT", "NVDA"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        finished = cli.work_queue(queue, pool, 2, 4, False, False, log=lambda *_: None)
    assert finished == "succeeded"
    assert sorted(queue.done) == ["AAPL", "ASML", "MSFT", "NVDA", "TSM"]
    assert queue.done["TSM"]["filings"] == 2 and queue.done["TSM"]["ai_calls"] == 7
    # Never more companies claimed at once than the worker reads at once.
    assert queue.calls.count("claim") == 6


def test_a_lost_pool_stops_the_worker_without_reporting_what_it_had_not_finished(monkeypatch):
    def read(control, symbol, *_):
        if symbol == "ASML":
            raise BrokenProcessPool("a company process was killed")
        return SymbolResult(symbol, filings=1)
    monkeypatch.setattr(cli, "process_symbol", read)
    queue = Queue(["TSM", "ASML", "AAPL"])
    with ThreadPoolExecutor(max_workers=1) as pool, pytest.raises(BrokenProcessPool):
        cli.work_queue(queue, pool, 1, 4, False, False, log=lambda *_: None)
    # TSM was reported; ASML is still held, so the control plane gives it (and AAPL) to the next worker.
    assert list(queue.done) == ["TSM"] and queue.working == {"ASML"} and queue.waiting == ["AAPL"]


def test_a_company_taken_back_from_this_worker_is_not_recorded_twice(monkeypatch):
    logged: list[str] = []
    queue = Queue(["TSM"])
    def read(control, symbol, *_):
        queue.working.discard(symbol)  # given up for lost while this worker was still reading it
        return SymbolResult(symbol)
    monkeypatch.setattr(cli, "process_symbol", read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        cli.work_queue(queue, pool, 1, 4, False, False, log=logged.append)
    assert queue.done == {} and "TSM: no longer this worker's; its result was not kept" in logged


def test_the_worker_renews_its_claim_while_companies_are_read(monkeypatch):
    import threading
    monkeypatch.setattr(cli, "RENEW_SECONDS", 0.05)
    release = threading.Event()
    monkeypatch.setattr(cli, "process_symbol", lambda control, symbol, *_: (release.wait(2), SymbolResult(symbol))[1])
    queue = Queue(["TSM"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        threading.Timer(0.3, release.set).start()
        cli.work_queue(queue, pool, 1, 4, False, False, log=lambda *_: None)
    assert queue.calls.count("renew") >= 3


def test_a_company_result_is_bounded_and_carries_the_memory_left():
    result = SymbolResult("TSM", errors=["x" * 900] * 8, q4_gaps=[str(year) for year in range(2000, 2020)])
    data = cli.company_result(result, 9.456)
    assert len(data["errors"]) == 5 and all(len(error) == 300 for error in data["errors"])
    assert len(data["q4_gaps"]) == 10 and data["lowest_available_gb"] == 9.46 and data["symbol"] == "TSM"


def test_a_manual_run_waits_for_off_peak_after_taking_its_run_and_keeps_it_alive():
    from datetime import datetime, timedelta, timezone
    clock = [datetime(2026, 11, 9, 3, 57, 30, tzinfo=timezone.utc)]  # a Monday, 2.5 minutes before peak hours end
    def sleep(seconds):
        clock[0] += timedelta(seconds=seconds)
    queue = Queue([])
    cli.wait_for_offpeak(queue, log=lambda *_: None, sleep=sleep, clock=lambda: clock[0])
    assert clock[0] >= datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
    assert queue.calls == ["renew", "renew", "renew"]  # every minute while waiting, and once at the end


def test_off_peak_runs_do_not_wait():
    from datetime import datetime, timezone
    queue = Queue([])
    cli.wait_for_offpeak(queue, sleep=lambda _s: (_ for _ in ()).throw(AssertionError("waited")),
                         clock=lambda: datetime(2026, 11, 9, 4, 30, tzinfo=timezone.utc))
    assert queue.calls == []
