"""One company end to end: XBRL breakdowns, the KPI list, earnings documents, the list's upkeep, then derived periods.

AI decides and points; code reads and proves. Every AI answer is checked before it changes anything stored:
- Flash reads each earnings document. When its reading matches where each KPI was found last quarter and every check
  passes, it stands. Otherwise the reasoning reader reads the document independently and values stand only where the two agree or
  where the reasoning reader's review picks one of the two readings.
- The reasoning reader picks the rows of an XBRL breakdown the rules cannot reconcile; the rows must add up to reported revenue.
- The reasoning reader explains a flagged jump; the explanation counts only with a quote found in the document.
- The reasoning reader audits each new quarter against the KPI list and may add KPIs or retire ones no longer reported.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from .archive import Archive
from .control import ControlError
from .document import Document
from .sec import FilingRef, company_profile, earnings_releases, periodic_reports
from .steps.shared import RecordBuilding, _folder, list_parents, only_adds
from .steps.loading import FilingLoading
from .steps.reports import ReportReading
from .steps.financial_statements import StatementReading
from .steps.chart_slides import ChartReading
from .steps.release_tables import ReleaseTableReading
from .steps.tagged_kpis import TaggedKpiReading
from .steps.listing_history import ListingHistory
from .steps.kpi_list import KpiListUpkeep
from .steps.release_flow import ReleaseFlow
from .steps.ai_reading import AiReading
from .steps.series_identity import SeriesIdentity
from .steps.series_checks import SeriesChecks
from .store import CompanyStore, FilingReading

_PASSED_OVER = "not an earnings document: no exhibit, or furnished outside the weeks after a quarter end"
LOCK_WAITS, LOCK_WAIT_SECONDS = 20, 30
# Stamped on every periodic report read; a report read by an older reader is read again (from the archive, no AI)
# so a fix to the XBRL reader reaches the whole history.
# Reports parsed ahead of the one being read, and parsed reports kept in memory.
_READ_AHEAD = 3

@dataclass
class SymbolResult:
    symbol: str
    filings: int = 0
    values: int = 0
    needs_review: int = 0
    ai_calls: int = 0
    second_reads: int = 0
    flash_only_reads: int = 0
    replayed_reads: int = 0
    # Breakdown years with a full year and three quarters but no fourth: surfaced in the run summary, never silent.
    q4_gaps: list[str] = field(default_factory=list)
    skipped: bool = False
    # Values read from earnings documents that are not rejected, and how many of them are verified (shown to users).
    release_values: int = 0
    release_verified: int = 0
    # KPIs the list cap took off (operating metrics only), so a lost metric is never silent.
    trimmed_kpis: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # What the run changed among the figures served (see CompanyStore.served_changes).
    served_changes: dict = field(default_factory=dict)



class SymbolPipeline(FilingLoading, ReportReading, StatementReading, ChartReading, ReleaseTableReading,
                     TaggedKpiReading, ListingHistory, KpiListUpkeep, ReleaseFlow, AiReading,
                     SeriesIdentity, SeriesChecks, RecordBuilding):
    """The run order, and every write of a step's rows: steps read through self.values and self.filings (read-only) and
    hand back what they want written."""

    def __init__(self, control, symbol: str, quarters: int, force: bool, today: date | None = None, log=print,
                 wait_for_lock: bool = False, report_quarters: int | None = None):
        self.control = control
        self.symbol = symbol
        self.force = force
        self.quarters = quarters
        # Earnings releases cost AI to read, so they keep the run's window; 10-Qs and 10-Ks are read by code from XBRL
        # and may reach further back (report_quarters).
        self.since = (today or date.today()) - timedelta(days=92 * quarters + 120)
        self.reports_since = (today or date.today()) - timedelta(days=92 * max(quarters, report_quarters or 0) + 120)
        self.log = log
        self.result = SymbolResult(symbol)
        self.store = CompanyStore(control, symbol, self.result, log)
        self.names: dict = {}
        self._xbrl_cache: dict[str, Future] = {}
        self._xbrl_used: set[str] = set()
        self._archiving: list[Future] = []
        # Releases are read several at a time: state changes one reader at a time under this lock, and a reader lets go
        # of it only while it waits on the AI or a download (see _waiting).
        self._state = threading.Lock()
        self._local = threading.local()
        self._dead: set[str] | None = None  # groups not read this run (see _readable)
        self._newest_release: date | None = None
        self.archive: Archive | None = None
        self._exhibits: dict[str, Future] = {}
        # Downloads and XBRL parsing start ahead of the filing being worked on.
        self._prefetch = ThreadPoolExecutor(max_workers=3)
        self._documents: dict[str, Document] = {}
        self._prompt_documents: dict[str, str] = {}
        self._curations: dict[tuple, list[str]] = {}
                # A Refresh waits for a run already working on the company instead of being skipped.
        self.wait_for_lock = wait_for_lock

    @property
    def values(self):
        return self.store.values

    @property
    def filings(self):
        return self.store.filings

    @property
    def company(self) -> str:
        return f"{self.profile.name} ({self.symbol})"

    def run(self) -> SymbolResult:
        try:
            return self._run()
        finally:
            self._prefetch.shutdown(wait=False, cancel_futures=True)
            try:
                # Released as soon as this company is done, not when the whole run finishes.
                self.control.call("release", symbol=self.symbol)
            except ControlError:
                pass  # the run's finish releases it anyway

    def _run(self) -> SymbolResult:
        state = self.control.call("symbol_state", symbol=self.symbol)
        for _attempt in range(LOCK_WAITS if self.wait_for_lock else 0):
            if not state.get("locked"):
                break
            self.log(f"{self.symbol}: another run is working on this company; waiting")
            time.sleep(LOCK_WAIT_SECONDS)
            state = self.control.call("symbol_state", symbol=self.symbol)
        if state.get("locked"):
            # Another run is working on this company; two at once would pay twice and overwrite each other.
            self.result.skipped = True
            self.log(f"{self.symbol}: another run is working on this company; skipped")
            return self.result
        self._curations = {(row["group_key"], row["members_key"]): list(row["chosen"])
                           for row in state.get("curations") or []}
        self.store.load(state.get("values") or [], state.get("filings") or [], state.get("ownerRejected") or [])
        self.names = (state.get("spec") or {}).get("names") or {}
        # Which filing gave each stored statement period: a quarter read from releases belongs to the newest release
        # that presents it.
        self._statement_owners = {
            (row["statement"], row["fiscal_year"], row["fiscal_period"], row["duration"]): row
            for row in state.get("statements") or []}
        self.profile, company = company_profile(self.symbol)
        self.archive = Archive(self.control, self.symbol, self.profile.cik, self.log)
        self.archive._listing()  # once, before the download threads start
        self.archive.label(self.symbol, self.profile.name)
        known_urls = {filing["accession"]: filing["source_url"] for filing in self.filings.values() if filing.get("source_url")}
        reports = sorted(periodic_reports(company, self.reports_since, known_urls), key=lambda ref: ref.filed)
        pending = [ref for ref in reports if self.force or self._pending(ref)]
        for ref in reversed(reports):
            if ref not in pending and not self.archive.has(_folder(ref), "xbrl.json"):
                # Read before the archive existed: stored now, once, without parsing it.
                self._archiving.append(self._prefetch.submit(self._archive_xbrl, ref))
        # The newest first (it gives the fiscal year's offset), then the rest a few ahead of the one being read: all at
        # once, a full re-read held every parsed report (about 2 GB a company; six companies took the runner down).
        for ref in pending[-1:] + pending[:_READ_AHEAD]:
            self._read_ahead(ref)
        # The earnings releases are listed while the reports are read: the listing waits only on SEC and the archive.
        passed: list[FilingRef] = []
        listing = self._prefetch.submit(earnings_releases, company, self.profile, self.since, self.archive, passed, known_urls)
        self.offset = self._year_offset(reports)
        for ref in reports:
            later = [other for other in pending if other.filed > ref.filed or (other.filed == ref.filed and other is not ref)]
            for other in later[:_READ_AHEAD]:
                self._read_ahead(other)
            reading = self._read_report(ref)
            if reading is not None:
                self.store.record_or_fail(reading)
        releases = [ref for ref in sorted(listing.result(), key=lambda ref: ref.filed) if self._is_earnings_document(ref)]
        for ref in passed:
            if ref.accession not in self.filings:
                self.store.record(FilingReading(ref, "skipped", notes=[_PASSED_OVER], counted=False))
        self._newest_release = max((ref.period_end for ref in releases if ref.period_end), default=None)
        self._store_release_statements(reports, releases)
        if self._no_quarterly_reports(reports):
            self._write_extra(self._read_titled_pies(releases), "pie figures")
        for rows in self._read_chart_breakdowns(releases):
            self._write_extra(rows, "chart figures")
        self.store.write(self._bare_shares())
        self._write_extra(self._read_release_breakdowns(releases), "release breakdowns")
        for ref in releases:
            if self.force or self._pending(ref):
                for exhibit in ref.exhibits[:6]:
                    self._exhibit(exhibit)
        spec, version, proposed = self._spec(state.get("spec"), releases)
        self._owner_edited = bool((state.get("spec") or {}).get("owner_edited_at"))
        if spec is not None and not proposed:
            self._carry_owner_additions(spec, version, state.get("previousSpec"), releases)
        if spec is not None:
            processed = self._process_releases(releases, spec, version)
            latest_end = releases[-1].period_end if releases else None
            # An evaluation on stored lists scores the readers, so it keeps the list as stored (no audit call).
            audit = os.environ.get("KPI_EVAL_LISTS") != "stored"
            if audit and not proposed and any(ref.period_end == latest_end for ref in processed):
                previous_spec = spec
                updated = self._maintain(spec, version, [ref for ref in releases if ref.period_end == latest_end])
                if updated is not None:
                    spec, version = updated
                    if only_adds(previous_spec, spec):
                        # New KPIs are looked for in the last four quarters; older filings keep their readings and
                        # simply move to the new list version (a full re-read took TSMC 38 of 63 minutes).
                        cutoff = latest_end - timedelta(days=370)
                        for ref in releases:
                            if ref.period_end < cutoff:
                                self._carry_to_version(ref, version)
                        self._process_releases([ref for ref in releases if ref.period_end >= cutoff], spec, version)
                    else:
                        self._process_releases(releases, spec, version)
        prospectus = self._prospectus(company, reports)
        # Each breakdown's year before its first report, from that report's comparatives (a recent listing; Brookfield
        # Asset Management, filing 10-Qs since 2025).
        self.store.write(self._year_ago_quarters())
        self.store.write(self._retire_named_remainders())
        if prospectus is not None:
            self._store_prospectus_breakdowns(prospectus)
            if spec is not None:
                self._read_prospectus_quarters(prospectus, releases, spec, version)
            # After the prospectus, whose audited figures stand where both give a quarter.
            self.store.write(self._fill_release_columns(releases))
        if spec is not None:
            self.store.write(self._adopt_listed_keys(spec))
            self.store.write(self._fill_tagged_metrics(reports, spec))
            self.store.write(self._fill_quarter_tables(releases))
            self.store.write(self._fill_quoted_rows(releases))
        # Across periods, in this order: each step sees what the one before wrote.
        self.store.write(self._unfile_quarters())
        self.store.write(self._refile_annuals())
        self.store.write(self._recheck_flagged())
        self.store.write(self._release_held_breakdowns())
        self.store.write(self._anchor_ai_breakdowns())
        self.store.write(self._prove_flagged(releases, list_parents(spec)))
        self.store.write(self._release_held_breakdowns())
        self.store.write(self._join_custom_axes())
        self.store.write(self._unify_series())
        self.store.write(self._unify_release_series(releases))
        self.store.write(self._adopt_restated_quarters())
        for rows in self._fix_mislabelled_quarters():
            self.store.write(rows)
        self.store.write(self._derive(spec))
        self.store.write(self._harmonize_labels())
        self.result.q4_gaps = self._q4_gaps()
        self.result.served_changes = self.store.served_changes()
        if self.result.served_changes:
            self.log(f"{self.symbol}: served figures changed: {self.result.served_changes}")
        live = [v for v in self.values.values() if v["method"] == "ai" and v["validation_status"] != "rejected"]
        self.result.release_values = len(live)
        self.result.release_verified = sum(1 for v in live if v["validation_status"] == "verified")
        if len(live) >= 5 and self.result.release_verified < 0.5 * len(live):
            self.log(f"{self.symbol}: only {self.result.release_verified} of {len(live)} release values are verified")
        if self.result.q4_gaps:
            self.log(f"{self.symbol}: no Q4 for {', '.join(self.result.q4_gaps[:10])}")
        for future in self._archiving:
            future.exception()  # archiving finishes before the download threads are shut down
        self.log(f"{self.symbol}: filing archive {self.archive.reads} read, {self.archive.writes} added")
        return self.result

    def _write_extra(self, rows: list[dict], what: str) -> None:
        """Figures a filing's report will replace or that only add to it: failing to store them never holds up the KPIs."""
        try:
            self.store.write(rows)
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: {what} not stored ({type(error).__name__}: {error})"[:300])

    def _store_prospectus_breakdowns(self, ref: FilingRef) -> None:
        reading, renamed = self._read_prospectus_breakdowns(ref)
        if reading is None:
            return
        try:
            self.store.record(reading)
            self.store.write(self._join_prospectus_names(renamed))
        except Exception as error:  # noqa: BLE001 - the history is extra
            self.log(f"{self.symbol}: prospectus breakdowns not stored ({type(error).__name__}: {error})"[:300])
