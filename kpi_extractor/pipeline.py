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

import copy
import math
import os
import re
import time
import string
import threading
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from itertools import permutations
from datetime import date, timedelta

from . import ai
from .ai import AiResponseError, Located, Spec
from .archive import Archive, filing_folder, xbrl_from_parts, xbrl_parts
from .control import ControlError
from .derive import balancing, derive_periods
from .document import Document, clean, parse_document, parse_number
from .extract import (MAX_MIX_JUMP, MAX_RATIO_JUMP, ReadValue, _listed_kpi, check_period, column_header, count_jump, describe,
                      locator_hint, read_values, validate_groups)
from .fiscal import fiscal_label, learn_year_offset
from .charts import (ChartBreakdown, ChartUnits, breakdown_slides, ocr_words, read_breakdowns, read_growth_rates, read_titled_pie,
                     read_units,
                     titled_pie_slides, unit_region)
from .prospectus import interim_quarters, prospectus_breakdowns, quarter_sections
from .release_breakdowns import line_key, release_breakdown
from .segment_income import (GROUP_KEY as INCOME_GROUP, LEFT_OVER_KEY, REVENUE_GROUP_KEY as OWN_REVENUE_GROUP, segment_income,
                             segment_revenue)
from .release_columns import amount, earlier_quarters, quarter_table_rows, release_grids, row_key, row_numbers
from .tagged_metrics import (agrees, fact_lines, learn_tag, learn_tags, quarter_facts, tag_from_locator, unit_fits,
                             year_ago_facts)
from .release_statements import release_headline, release_statements, reported_units
from .statements import statement_lines
from .replay import replay
from .sec import (FilingRef, classification_excerpt, company_profile, earnings_releases, exhibit_html, ipo_prospectus,
                  is_own_document, periodic_reports)
from .xbrl import XbrlCandidate, extract_breakdowns, member_children, member_key, official_labels

# About 30k tokens: enough for any earnings release, and for the revenue sections of a full financial report.
MAX_DOCUMENT_CHARS = 120_000
AI_GROUP_PREFIX = "kpi_"
# Values read from a slide's chart: their own method, so a re-read of the release's text never withdraws them.
CHART_METHOD = "chart"
# A breakdown slide gives this many things a quarter (three pies and the units sold); a quarter with fewer is looked
# at again.
CHART_GROUPS = 4
# The bar chart of units is read again at these enlargements; a count is trusted when readings agree.
UNIT_SCALES = (3, 4)
# The enlargement a slide is read again at when a pie is missing from the first reading.
PIE_SCALE = 3
_SYSTEMS_SOLD = re.compile(r"lithography systems sold", re.I)
AI_GROUP_ORDER = 20
_DONE = {"processed", "needs_review", "skipped"}
# The quarters a refresh or a scheduled run covers; Read filings reaches further (40).
REFRESH_QUARTERS = 20
# The most attempts the store accepts for a filing: a count past it would fail the company's whole store.
_STORED_ATTEMPTS = 100
_MAX_ATTEMPTS = 3
_NOT_EARNINGS = "classified as not an earnings document"
# A 6-K read from its own document is classified from the report after its cover; one judged on the cover before is
# asked again once (TSMC's 2016–2019 results and financial statements were all judged on the cover).
_OWN_6K_DOCUMENT = re.compile(r"6-?k", re.I)
_CLASSIFIED_PAST_COVER = "classified past the cover 2"  # 2: covers written with &nbsp; too
_PASSED_OVER = "not an earnings document: no exhibit, or furnished outside the weeks after a quarter end"
_AI_PENDING = "AI review unavailable; retried next run"
_READ_BY_AI = "read by AI"
MAX_YEAR_RATIO = 3.0
LOCK_WAITS, LOCK_WAIT_SECONDS = 20, 30
_READ_BY_REPLAY = "read by replay"
_NOT_REPORTED = "not reported: "
# Stamped on every periodic report read; a report read by an older reader is read again (from the archive, no AI)
# so a fix to the XBRL reader reaches the whole history.
# Reports parsed ahead of the one being read, and parsed reports kept in memory.
_READ_AHEAD = 3
_XBRL_KEPT = 6
_XBRL_READER = "xbrl reader 39"  # 21: segment operating income
# Stamped on every earnings release read. A release an older reader left with a tracked group unread is read again
# when this changes (the newest quarter's locators are replayed first, so a fixed reader seldom needs the AI): bump it
# with a release-reader fix, e.g. ASML's slide statements, which once left 2025's quarters unread. Releases read before
# stamping began are never re-read by it, so a bump cannot re-read every company's history.
_RELEASE_READER = "release reader 2"
_RELEASE_READER_PREFIX = "release reader "
_PROSPECTUS_READER = "prospectus reader 1"
# Segment operating income sorts after the revenue breakdowns, and the revenue-only steps (release and prospectus
# figures, the repeat guard) leave it out.
INCOME_ORDER = 50
# Evidence that settles a flagged value without anyone looking at it; it outranks the check of a read total row.
_EQUALS_XBRL = "equals a figure reported in XBRL"
_ADDS_TO_XBRL = "parts add up to a figure reported in XBRL"
_CONFIRMED_LATER = "confirmed by the next year's release"
_RESTATED_LATER = "restated in the next year's release"
_WITHIN_XBRL = "parts make up nearly all of a figure reported in XBRL"
_PROOFS = (_EQUALS_XBRL, _ADDS_TO_XBRL, _CONFIRMED_LATER, _RESTATED_LATER, _WITHIN_XBRL)
# A split of part of a reported line (Adobe's two customer groups: 99.5% of its subscription revenue, the rest its
# advertising business's) covers at least this share of it.
_MIN_COVERAGE = 0.99
_EXACT_NOTE = re.compile(r"^the [\w-]+'s exact (?:figure|year less its three quarters);")
_UNANCHORED = {"total matches no figure the company reported in XBRL", "no total revenue to reconcile against"}
# Restatements move a figure a little (a reclassification); a jump beyond this is a different column, not a restatement.
MAX_RESTATEMENT = 0.10
# Releases a company's run reads at once, after its newest (see _process_releases).
RELEASE_READERS = 4
# A group read this many quarters without one verified period is not read again (see _readable).
DEAD_GROUP_QUARTERS = 8


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


def value_key(value: dict) -> tuple[str, str, str, str]:
    return value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"]


class SymbolPipeline:
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
        self.filings: dict[str, dict] = {}
        self.values: dict[tuple, dict] = {}
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
        self.filings = {filing["accession"]: filing for filing in state.get("filings") or []}
        self.values = {value_key(value): value for value in state.get("values") or []}
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
        reports = sorted(periodic_reports(company, self.reports_since), key=lambda ref: ref.filed)
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
        listing = self._prefetch.submit(earnings_releases, company, self.profile, self.since, self.archive, passed)
        self.offset = self._year_offset(reports)
        for ref in reports:
            later = [other for other in pending if other.filed > ref.filed or (other.filed == ref.filed and other is not ref)]
            for other in later[:_READ_AHEAD]:
                self._read_ahead(other)
            self._process_report(ref)
        releases = [ref for ref in sorted(listing.result(), key=lambda ref: ref.filed) if self._is_earnings_document(ref)]
        for ref in passed:
            if ref.accession not in self.filings:
                self._store_filing(ref, "skipped", notes=[_PASSED_OVER], counted=False)
        self._newest_release = max((ref.period_end for ref in releases if ref.period_end), default=None)
        self._store_release_statements(reports, releases)
        self._store_chart_breakdowns(releases)
        self._store_release_breakdowns(releases)
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
        self._year_ago_quarters()
        if prospectus is not None:
            self._store_prospectus_breakdowns(prospectus)
            if spec is not None:
                self._read_prospectus_quarters(prospectus, releases, spec, version)
            # After the prospectus, whose audited figures stand where both give a quarter.
            self._fill_release_columns(releases)
        if spec is not None:
            self._adopt_listed_keys(spec)
            self._fill_tagged_metrics(reports, spec)
            self._fill_quarter_tables(releases)
            self._fill_quoted_rows(releases)
        self._refile_annuals()
        self._recheck_flagged()
        self._anchor_ai_breakdowns()
        self._prove_flagged(releases)
        self._join_custom_axes()
        self._unify_series()
        self._unify_release_series(releases)
        self._adopt_restated_quarters()
        self._fix_mislabelled_quarters()
        self._derive(spec)
        self._harmonize_labels()
        self.result.q4_gaps = self._q4_gaps()
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

    # Periodic reports: official XBRL breakdowns, with rows chosen by the AI where the rules fall short.

    def _pending(self, ref: FilingRef) -> bool:
        """Whether a filing still has to be read (a spec change can still re-read a finished release)."""
        previous = self.filings.get(ref.accession)
        return not previous or (previous["status"] not in _DONE and previous.get("attempts", 0) < _MAX_ATTEMPTS) or (
            ref.role == "periodic_report" and _XBRL_READER not in (previous.get("notes") or []))

    def _read_ahead(self, ref: FilingRef) -> None:
        if ref.accession not in self._xbrl_cache:
            self._xbrl_cache[ref.accession] = self._prefetch.submit(self._load_xbrl, ref)

    def _xbrl(self, ref: FilingRef):
        # The most recently used last; beyond a few, the least recently used are dropped (read again from the archive
        # if needed). One parsed ahead and not yet used is never dropped.
        future = self._xbrl_cache.pop(ref.accession, None) or self._prefetch.submit(self._load_xbrl, ref)
        self._xbrl_cache[ref.accession] = future
        self._xbrl_used.add(ref.accession)
        used = [accession for accession in self._xbrl_cache if accession in self._xbrl_used]
        for accession in used[:max(0, len(used) - _XBRL_KEPT)]:
            del self._xbrl_cache[accession]
            self._xbrl_used.discard(accession)
        return future.result()

    def _archive_xbrl(self, ref: FilingRef) -> None:
        # A filing without an XBRL instance is marked, so it is not downloaded again on every run.
        self.archive.write_json(_folder(ref), "xbrl.json", xbrl_parts(ref.filing) or _NO_XBRL)

    def _load_xbrl(self, ref: FilingRef):
        """A filing's XBRL from the archive, else from EDGAR and then archived."""
        if self.archive is None:
            return ref.filing.xbrl()
        stored = self.archive.read_json(_folder(ref), "xbrl.json")
        if stored and stored.get("parts"):
            return xbrl_from_parts(stored)
        if stored is not None:
            return ref.filing.xbrl()  # marked as having no instance to archive: EDGAR's own reading, as before
        parts = xbrl_parts(ref.filing)
        if parts is None:
            self.archive.write_json(_folder(ref), "xbrl.json", _NO_XBRL)
            return ref.filing.xbrl()
        self.archive.write_json(_folder(ref), "xbrl.json", parts)
        return xbrl_from_parts(parts)

    def _exhibit(self, exhibit) -> Future:
        if exhibit.url not in self._exhibits:
            self._exhibits[exhibit.url] = self._prefetch.submit(exhibit_html, exhibit)
        return self._exhibits[exhibit.url]

    def _year_offset(self, reports: list[FilingRef]) -> int:
        for ref in reversed(reports):
            try:
                xbrl = self._xbrl(ref)
            except Exception:  # noqa: BLE001 - an unreadable report just cannot vote
                continue
            if xbrl is None:
                continue
            entity = xbrl.entity_info or {}
            period_end = entity.get("document_period_end_date")
            if period_end and entity.get("fiscal_year"):
                return learn_year_offset(
                    [(date.fromisoformat(str(period_end)[:10]), str(entity["fiscal_year"]), False)],
                    self.profile.fiscal_year_end,
                )
        return 0

    def _process_report(self, ref: FilingRef) -> None:
        if not self.force and not self._pending(ref):
            return
        try:
            xbrl = self._xbrl(ref)
            if xbrl is None:
                # Judged by this reader: stamped, so later runs leave it (it was re-read every run until attempts ran out).
                self._store_filing(ref, "skipped", notes=["no XBRL", _XBRL_READER])
                return
            facts = xbrl.facts.to_dataframe()
            labels = official_labels(getattr(xbrl, "element_catalog", None))
            self._member_children = member_children(xbrl)
            breakdowns = extract_breakdowns(facts, xbrl.entity_info or {}, ref.form, labels, self._member_children)
            if breakdowns is None:
                self._store_filing(ref, "skipped", notes=["no document period", _XBRL_READER])
                return
            fiscal_year, fiscal_period = fiscal_label(breakdowns.period_end, self.profile.fiscal_year_end,
                                                      annual=breakdowns.annual, year_offset=self.offset)
            self._store_statements(ref, xbrl, breakdowns, fiscal_year, fiscal_period)
            # A company's own segment revenue measure is its revenue by segment, in place of the GAAP split of it.
            try:
                own_revenue = segment_revenue(facts, breakdowns.period_end, breakdowns.annual, labels, self._member_children)
            except Exception:  # noqa: BLE001 - the GAAP split stands
                own_revenue = None
            groups = [(group, []) for group in breakdowns.groups if not (own_revenue and group.key == OWN_REVENUE_GROUP)]
            taken = {group.key for group, _notes in groups} | ({OWN_REVENUE_GROUP} if own_revenue else set())
            ai_pending = False
            for candidate in breakdowns.rejected:
                if candidate.key in taken:
                    continue
                try:
                    group = self._curate(candidate)
                except ControlError:
                    # The AI is unavailable (e.g. out of credit): keep every reconciled breakdown now and let the
                    # next run retry the review, instead of losing the whole filing.
                    ai_pending = True
                    continue
                if group is not None:
                    groups.append((group, ["rows chosen by review"]))
                    taken.add(group.key)
            groups = [(self._continuing_split(group, breakdowns.period_end), notes) for group, notes in groups]
            records = []
            for group, extra_notes in groups:
                named = self.names.get(group.key) or {}
                # Rows that land on one key ("Other" and "All other") are parts of it: they add up.
                # Their nine months to date add up the same way, or Q4 = year − nine months would be wrong.
                merged: dict[str, list] = {}
                for reported, label, value in group.members:
                    # The reader already summed each key's nine-month figures; take them once per key.
                    if reported in merged:
                        merged[reported][2] += value
                    else:
                        merged[reported] = [reported, label, value, group.year_to_date.get(reported),
                                            group.prior_year_to_date.get(reported)]
                for order, (member, (reported, label, value, ytd, prior_ytd)) in enumerate(merged.items()):
                    records.append(self._record(
                        group_key=group.key, group_label=named.get("label") or group.label, group_kind="revenue_breakdown",
                        group_order=group.order, kpi_key=member, kpi_label=(named.get("members") or {}).get(member) or label,
                        kpi_order=order, unit="currency", value=value, currency=group.currency, method="xbrl",
                        validation_status="verified", reconciliation_error_pct=group.reconciliation_error,
                        notes=extra_notes, ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                        period_end=breakdowns.period_end, locator={
                            "concept": group.concept, "total": group.total, "member": group.elements.get(reported),
                            **({"parent": group.parents[reported]} if reported in group.parents else {}),
                            "prior": group.prior.get(reported),
                            **({"prior_total": group.prior_total} if group.prior_total else {}),
                            **({"ytd": ytd, "ytd_total": group.year_to_date_total}
                               if ytd is not None and group.year_to_date_total else {}),
                            **({"prior_ytd": prior_ytd, "prior_ytd_total": group.prior_year_to_date_total}
                               if prior_ytd is not None and group.prior_year_to_date_total else {}),
                        },
                    ))
            records = self._name_remainders(ref, records)
            # A split's subtotals (Alphabet's Google properties, Google advertising) in a group of their own: placed in the
            # tree by their parents, never added into a breakdown's sum.
            for group, extra_notes in groups:
                named = self.names.get(group.key) or {}
                for order, (member, label, value) in enumerate(group.levels):
                    ytd = group.level_year_to_date.get(member)
                    records.append(self._record(
                        group_key=f"{group.key}_levels", group_label=f"{named.get('label') or group.label} subtotals",
                        group_kind="metric", group_order=group.order, kpi_key=member,
                        kpi_label=(named.get("members") or {}).get(member) or label, kpi_order=order, unit="currency",
                        value=value, currency=group.currency, method="xbrl", validation_status="verified",
                        reconciliation_error_pct=None, notes=extra_notes, ref=ref, fiscal_year=fiscal_year,
                        fiscal_period=fiscal_period, period_end=breakdowns.period_end, locator={
                            "concept": group.concept, "member": group.elements.get(member), "subtotal": True,
                            "parent": group.parents.get(member), "prior": group.level_prior.get(member),
                            **({"ytd": ytd, "ytd_total": group.year_to_date_total} if ytd is not None and group.year_to_date_total else {}),
                        },
                    ))
            records += self._income_records(facts, labels, breakdowns, ref, fiscal_year, fiscal_period, groups, own_revenue)
            if records:
                # The release's figures for this quarter give way to the report's; a line the report does not give
                # (renamed, or not broken out) stops being served.
                fresh = {value_key(record) for record in records}
                records += [{**value, "validation_status": "rejected", "notes": ["replaced by the quarter's report"]}
                            for value in self.values.values()
                            if (value.get("locator") or {}).get("release_table") and value["validation_status"] != "rejected"
                            and (value["fiscal_year"], value["fiscal_period"]) == (fiscal_year, fiscal_period)
                            and value_key(value) not in fresh]
            status = "failed" if ai_pending else "processed" if records else "skipped"
            notes = [_XBRL_READER, *([_AI_PENDING] if ai_pending else [] if records
                                     else ["no revenue breakdown reconciles to reported revenue"])]
            self._store_filing(ref, status, records=records, period_end=breakdowns.period_end,
                               fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes)
        except Exception as error:  # noqa: BLE001 - one bad filing must not stop the company
            self._fail_filing(ref, error)

    def _name_remainders(self, ref: FilingRef, records: list[dict]) -> list[dict]:
        """A breakdown's worked-out remainder under the name the report's own table gives it: Salesforce's Q2 FY2027
        10-Q tags no "Hedging gains", which its tables list beside its regions and its service offerings. The row must
        sit in a table with at least two of the breakdown's other lines and equal the remainder to the table's rounding."""
        left = [record for record in records if record["kpi_key"] == LEFT_OVER_KEY]
        if not left:
            return records
        try:
            document = parse_document(ref.filing.html(), ref.source_url)
        except Exception:  # noqa: BLE001 - the remainder keeps its worked-out name
            return records
        names: dict[int, str] = {}
        for row in left:
            others = {line_key(record["kpi_label"]) for record in records if record["group_key"] == row["group_key"]
                      and record is not row}
            if name := _row_named(document, float(row["value"]), others):
                names[id(row)] = name
        return [{**record, "kpi_key": member_key(name), "kpi_label": name} if (name := names.get(id(record))) else record
                for record in records]

    def _income_records(self, facts, labels, breakdowns, ref: FilingRef, fiscal_year: str, fiscal_period: str,
                        groups, own_revenue=None) -> list[dict]:
        """Each segment's operating income, as tagged (see segment_income): its own breakdown, beside the revenue ones; and
        a company's own segment revenue measure, which its segment profit is measured against."""
        try:
            income = segment_income(facts, breakdowns.period_end, breakdowns.annual, labels, self._member_children)
        except Exception as error:  # noqa: BLE001 - the revenue breakdowns stand without it
            self.log(f"{self.symbol}: segment income of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
            return []
        records = self._measure_records(own_revenue, OWN_REVENUE_GROUP, 0, groups, ref, fiscal_year,
                                        fiscal_period, breakdowns.period_end) if own_revenue else []
        if income is None:
            return records
        prior = {key: value for key, _name, _element, value in income.prior.rows} if income.prior else {}
        # A year whose worked-out remainder is the one line its quarters name (AMD's 10-K tags "All Other" with the sign
        # its 10-Qs give it reversed, so nothing tagged closes the gap) keeps that name.
        keys = {key for key, *_rest in income.current.rows}
        quarters = [v for v in self.values.values() if v["group_key"] == INCOME_GROUP and v["validation_status"] == "verified"
                    and v["fiscal_year"] == fiscal_year and v["fiscal_period"] in ("Q1", "Q2", "Q3")]
        quarter_names = {v["kpi_key"]: v["kpi_label"] for v in quarters}
        if breakdowns.annual and len({v["fiscal_period"] for v in quarters}) == 3 \
                and keys - set(quarter_names) == {LEFT_OVER_KEY} and len(set(quarter_names) - keys) == 1:
            other = next(iter(set(quarter_names) - keys))
            income.current.rows = [(other, quarter_names[other], element, value) if key == LEFT_OVER_KEY
                                   else (key, label, element, value) for key, label, element, value in income.current.rows]
            if LEFT_OVER_KEY in prior:
                prior[other] = prior.pop(LEFT_OVER_KEY)
        return records + self._measure_records(income, INCOME_GROUP, INCOME_ORDER, groups, ref, fiscal_year, fiscal_period,
                                               breakdowns.period_end, prior=prior)

    def _measure_records(self, measure, group_key: str, group_order: int, groups, ref: FilingRef, fiscal_year: str,
                         fiscal_period: str, period_end, prior: dict | None = None) -> list[dict]:
        """A segment measure's rows (see segment_income), with the year before and the year to date in their locators."""
        currency = next((group.currency for group, _notes in groups if group.currency), None) or "USD"
        named = self.names.get(group_key) or {}
        ytd = {key: value for key, _name, _element, value in measure.year_to_date.rows} if measure.year_to_date else {}
        prior = prior if prior is not None else (
            {key: value for key, _name, _element, value in measure.prior.rows} if measure.prior else {})
        records = []
        for order, (key, label, element, value) in enumerate(measure.current.rows):
            records.append(self._record(
                group_key=group_key, group_label=named.get("label") or measure.label, group_kind="revenue_breakdown",
                group_order=group_order, kpi_key=key, kpi_label=(named.get("members") or {}).get(key) or label,
                kpi_order=order, unit="currency", value=value, currency=currency, method="xbrl", validation_status="verified",
                reconciliation_error_pct=0, notes=[], ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                period_end=period_end, locator={
                    "concept": measure.concept, "total": measure.current.total, "member": element,
                    **({"prior": prior[key], "prior_total": measure.prior.total} if key in prior else {}),
                    **({"ytd": ytd[key], "ytd_total": measure.year_to_date.total} if key in ytd else {}),
                },
            ))
        return records

    def _store_statements(self, ref: FilingRef, xbrl, breakdowns, fiscal_year: str, fiscal_period: str) -> None:
        """The filing's three statements as presented; a statement that cannot be read never holds up its KPIs."""
        try:
            lines, currency = statement_lines(xbrl, breakdowns.period_end, breakdowns.annual)
            if not lines:
                return
            currency = currency or next((group.currency for group in breakdowns.groups if group.currency), "USD")
            shared = {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                      "period_end": breakdowns.period_end.isoformat(), "form": ref.form,
                      "filed_at": ref.filed.isoformat(), "currency": currency}
            self.control.call("store_statements", symbol=self.symbol, accession=ref.accession,
                              lines=[{**line, **shared} for line in lines])
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: statements of {ref.form} {ref.filed} not stored ({type(error).__name__}: {error})"[:300])

    def _store_release_statements(self, reports: list[FilingRef], releases: list[FilingRef]) -> None:
        """Quarterly statements for a filer whose only XBRL is annual (a 20-F filer such as ASML): read from its
        earnings releases' own tables, no AI. Each quarter is in several releases (a five-quarter summary), so it is
        kept from the newest release that presents it; a release whose own quarter a newer one already gave is not
        even opened. A release that cannot be read never holds up the KPIs."""
        if not reports or any(ref.form.upper().startswith("10-Q") for ref in reports):
            return
        owners = self._statement_owners
        for ref in sorted(releases, key=lambda item: item.filed, reverse=True):
            if ref.period_end is None:
                continue
            try:
                own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                if any(held["accession"] == ref.accession and key[0] == "income" for key, held in owners.items()):
                    continue  # already read whole: it owns an income statement (an annual report's is its year)
                owner = owners.get(("income", *own, "quarter"))
                if owner and str(owner.get("filed_at") or "")[:10] >= ref.filed.isoformat():
                    continue
                lines: list[dict] = []
                for statement in release_statements(self._document(ref)):
                    if statement.currency is None:
                        continue  # amounts in more than one currency side by side (an offering document): not guessed
                    fiscal_year, fiscal_period = fiscal_label(statement.period_end, self.profile.fiscal_year_end,
                                                              year_offset=self.offset)
                    key = (statement.statement, fiscal_year, fiscal_period, statement.duration)
                    held = owners.get(key)
                    if held and held["accession"] != ref.accession and str(held.get("filed_at") or "")[:10] > ref.filed.isoformat():
                        continue  # a newer release already gave this quarter
                    shared = {"statement": statement.statement, "duration": statement.duration, "fiscal_year": fiscal_year,
                              "fiscal_period": fiscal_period, "period_end": statement.period_end.isoformat(),
                              "form": ref.form, "filed_at": ref.filed.isoformat(), "currency": statement.currency}
                    lines.extend({**line, **shared} for line in statement.lines)
                    owners[key] = {"accession": ref.accession, "filed_at": ref.filed.isoformat()}
                if lines:
                    self.control.call("store_statements", symbol=self.symbol, accession=ref.accession, lines=lines)
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} statements stored ({len(lines)} lines)")
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: statements of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
        self._store_headline(releases)
        self._store_titled_pies(releases)

    def _store_headline(self, releases: list[FilingRef]) -> None:
        """The newest quarter's selected income lines from its earnings release (TSMC's summary table), while that
        quarter has no full income statement yet: its consolidated report follows weeks later and replaces them."""
        latest = max((ref for ref in releases if ref.period_end), key=lambda item: item.filed, default=None)
        if latest is None:
            return
        try:
            own = fiscal_label(latest.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            owners = self._statement_owners
            if any(owners.get(("income", *own, duration)) for duration in ("quarter", "ytd", "year")):
                return
            if (owners.get(("income", *own, "headline")) or {}).get("accession") == latest.accession:
                return
            statement = release_headline(self._document(latest), latest.period_end)
            if statement is None:
                return
            shared = {"statement": "income", "duration": "headline", "fiscal_year": own[0], "fiscal_period": own[1],
                      "period_end": statement.period_end.isoformat(), "form": latest.form,
                      "filed_at": latest.filed.isoformat(), "currency": statement.currency}
            self.control.call("store_statements", symbol=self.symbol, accession=latest.accession,
                              lines=[{**line, **shared} for line in statement.lines])
            owners[("income", *own, "headline")] = {"accession": latest.accession, "filed_at": latest.filed.isoformat()}
            self.log(f"{self.symbol}: {latest.form} {latest.filed} headline figures stored ({len(statement.lines)} lines)")
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: headline of {latest.form} {latest.filed} not read ({type(error).__name__}: {error})"[:300])

    def _store_titled_pies(self, releases: list[FilingRef]) -> None:
        """A quarter's platform split from its results-day slide (TSMC's "3Q26 Revenue by Platform"), read by the local
        OCR model weeks before the quarter's report gives the amounts. The slide's growth chart grows each platform's
        reported amount of the quarter before ("+22%"), scaled to the quarter's net revenue, which the release states;
        the pie's rounded shares must agree with the result. Whole-point shares alone would misstate the small
        platforms (a 4% slice is anything from 3.5% to 4.5%). The figures go into the series the company's reports fill
        (matched by the members' names), and the report's own figures replace them. Only a quarter it lacks is read.

        The technology pie is a split of wafer revenue, an amount the release does not give: its shares are stored as
        shares (Bedrock's percentage chart shows them) until the report's amounts replace them."""
        for ref in sorted((ref for ref in releases if ref.period_end), key=lambda item: item.filed, reverse=True)[:2]:
            try:
                quarter = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                slides = []
                for exhibit in ref.exhibits[:6]:
                    raw = self._waiting(self._exhibit(exhibit).result)
                    slides.extend((urllib.parse.urljoin(exhibit.url, source), axis, year, period)
                                  for source, axis, year, period in titled_pie_slides(raw or ""))
                records: list[dict] = []
                for link, axis, year, period in slides:
                    if (year, period) != quarter:
                        continue
                    group = self._series_for(axis)
                    # Read once: the quarter's report, or this reading already done (figures stored with their rates).
                    if group is None or any(value["group_key"] == group and (value["fiscal_year"], value["fiscal_period"]) == quarter
                                            and value["validation_status"] == "verified"
                                            and (value["method"] != CHART_METHOD or {"growth", "share_of"} & set(value.get("locator") or {}))
                                            for value in self.values.values()):
                        continue
                    words = ocr_words(_fetch_image(link))
                    pie = read_titled_pie(words, axis, year, period)
                    if pie is None:
                        continue
                    if axis == "technology":
                        records.extend(self._technology_share_records(pie, group, ref, link))
                        continue
                    growth, base = read_growth_rates(words), self._release_revenue(ref)
                    if growth is None or base is None:
                        continue
                    records.extend(self._titled_pie_records(pie, growth, group, base, ref, link))
                if records:
                    self._store(values=records)
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} pie figures stored ({len(records)} values)")
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: pies of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])

    def _store_release_breakdowns(self, releases: list[FilingRef]) -> None:
        """The newest quarter's segment and product revenue from its earnings release, while its report is not filed
        (Alphabet's 10-Q follows its release by days): each breakdown the reports gave last quarter, its lines found in
        a table of the release by name and checked against the year-ago column and total revenue. The report's XBRL
        replaces them; a breakdown the release does not give (Alphabet's regions) waits for the report."""
        latest = max((ref for ref in releases if ref.period_end), key=lambda item: item.filed, default=None)
        if latest is None:
            return
        try:
            own = fiscal_label(latest.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            live = [value for value in self.values.values() if value["validation_status"] == "verified"
                    and value["fiscal_period"] != "FY" and value["unit"] == "currency"]
            if any(value["method"] == "xbrl" and (value["fiscal_year"], value["fiscal_period"]) == own for value in live):
                return  # the report is in
            stored = {value["group_key"] for value in live if (value["fiscal_year"], value["fiscal_period"]) == own
                      and (value.get("locator") or {}).get("release_table") and value["source_accession"] == latest.accession}
            reported = [value for value in live if value["method"] in ("xbrl", "derived")
                        and (value["fiscal_year"], value["fiscal_period"]) < own]
            if not reported:
                return
            before = max((value["fiscal_year"], value["fiscal_period"]) for value in reported)
            year_ago = (str(int(own[0]) - 1), own[1])
            groups: dict[str, list[dict]] = {}
            for value in reported:
                if (value["fiscal_year"], value["fiscal_period"]) == before:
                    groups.setdefault(value["group_key"], []).append(value)
            past = {(value["group_key"], value["kpi_key"]): float(value["value"]) for value in reported
                    if (value["fiscal_year"], value["fiscal_period"]) == year_ago}
            document = self._document(latest)
            records: list[dict] = []
            tables: dict[str, str] = {}
            # Subtotal groups ("…_levels") last: their lines are looked for in the table their breakdown came from.
            for key in sorted(groups, key=lambda item: item.endswith("_levels")):
                if key in stored:
                    continue
                lines = groups[key]
                levels = key.endswith("_levels")
                within = [tables[key.removesuffix("_levels")]] if levels and key.removesuffix("_levels") in tables else None
                if levels and within is None:
                    continue
                found = release_breakdown(document, {line["kpi_key"]: line["kpi_label"] for line in lines},
                                          {line["kpi_key"]: past[(key, line["kpi_key"])] for line in lines
                                           if (key, line["kpi_key"]) in past},
                                          latest.period_end, tables=within, totalled=not levels,
                                          # Segment operating income from the same table's "Operating income" rows.
                                          measure="income" if key == INCOME_GROUP else "revenue")
                if found is None:
                    continue
                tables[key] = found.table
                total = None if levels else found.total
                gap = round((sum(found.values.values()) - total) / total * 100, 4) if total else None
                for line in lines:
                    member = line["kpi_key"]
                    parent = (line.get("locator") or {}).get("parent")
                    records.append(self._record(
                        group_key=key, group_label=line["group_label"], group_kind=line["group_kind"],
                        group_order=line["group_order"], kpi_key=member, kpi_label=line["kpi_label"],
                        kpi_order=line["kpi_order"], unit="currency", value=found.values[member],
                        currency=line.get("currency"), method=CHART_METHOD, validation_status="verified",
                        reconciliation_error_pct=gap, notes=["from the earnings release; the quarter's report replaces it"],
                        ref=latest, fiscal_year=own[0], fiscal_period=own[1], period_end=latest.period_end,
                        locator={"release_table": found.table, "row": found.rows[member],
                                 **({"total": total} if total is not None else {}),
                                 **({"subtotal": True} if levels else {}),
                                 **({"parent": parent} if parent else {}),
                                 **({"prior": found.prior[member]} if member in found.prior else {})},
                    ))
            if records:
                self._store(values=records)
                self.log(f"{self.symbol}: {latest.form} {latest.filed} release breakdowns stored ({len(records)} values)")
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: breakdowns of {latest.form} {latest.filed} not read ({type(error).__name__}: {error})"[:300])

    def _fill_tagged_metrics(self, reports: list[FilingRef], spec: Spec) -> None:
        """Quarters of an operating KPI its releases do not state, from the reports' XBRL where the KPI is tagged
        (Microsoft's commercial remaining performance obligation, in its releases only from FY2026). The tag is learned
        from quarters both give (see tagged_metrics); a quarter the release states, or the owner decided, is left as it
        is. Reports are read newest first, and a KPI's history ends where two reports in a row do not tag it."""
        quarters: dict[tuple[str, str], tuple[FilingRef, date]] = {}
        for ref in reports:
            # The stored filing knows its period end (not its fiscal label: the control plane does not return it).
            end = (self.filings.get(ref.accession) or {}).get("period_end")
            if not end:
                continue
            end = date.fromisoformat(str(end)[:10])
            try:
                year, period = fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
            except Exception:  # noqa: BLE001 - a period off the company's calendar is left out
                continue
            quarters[(year, period)] = (ref, end)
        if not quarters:
            return
        facts: dict[tuple[str, str], dict] = {}

        def facts_of(quarter: tuple[str, str]) -> dict:
            if quarter not in facts:
                ref, end = quarters[quarter]
                try:
                    xbrl = self._xbrl(ref)
                    facts[quarter] = quarter_facts(xbrl.facts.to_dataframe(), end) if xbrl is not None else {}
                except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                    facts[quarter] = {}
            return facts[quarter]

        newest_first = sorted(quarters, reverse=True)
        records: list[dict] = []
        unread: list[tuple] = []
        picked: dict[str, object] = {}
        for _pass in range(2):
            records += self._tagged_records(spec, quarters, newest_first, facts_of, facts, unread, picked)
            if _pass or not unread:
                break
            picked = self._pick_tags(unread, quarters, newest_first, facts_of)
            if not picked:
                break
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records)} KPI values read from reports' XBRL where the releases do not give them")

    def _fill_quarter_tables(self, releases: list[FilingRef]) -> None:
        """A metric's missing quarters from the quarter-headed tables releases carry (ASML's slides give five quarters of
        lithography systems sold; its KPI, added later, was read only from the latest releases). A row is found by the
        names the KPI's readings give it and taken only when another of its columns equals a figure already held, which
        also gives its scale; each quarter filled this way vouches for the next table. A quarter's own release is
        preferred; one held in any way is never replaced, and quarters before the KPI's first are not looked for."""
        held: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
        for v in self.values.values():
            if v["group_kind"] == "metric" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                held.setdefault((v["group_key"], v["kpi_key"]), {})[(v["fiscal_year"], v["fiscal_period"])] = v
        own = {}
        for ref in releases:
            quarter = self._quarter_of(ref.period_end) if ref.period_end else None
            if quarter and quarter[1] != "FY":
                own.setdefault(quarter, ref)
        index = lambda quarter: int(quarter[0]) * 4 + int(quarter[1][1])  # noqa: E731
        wanted: dict[tuple[str, str], tuple[list[str], set[tuple[str, str]]]] = {}
        for kpi, rows in held.items():
            verified = [v for v in rows.values() if v["validation_status"] == "verified" and v["method"] == "ai"]
            labels = {label for v in verified if (label := _row_label(v.get("locator") or {}))}
            first = min((index(quarter) for quarter in rows), default=None)
            gaps = {quarter for quarter in own if first is not None and index(quarter) > first and quarter not in rows}
            if labels and gaps:
                wanted[kpi] = (sorted(labels), gaps)
        if not wanted:
            return
        reach = {quarter for _labels, gaps in wanted.values() for gap in gaps for quarter in own
                 if 0 <= index(quarter) - index(gap) <= 4}
        texts: dict[str, list[str]] = {}
        for quarter in sorted(reach)[-40:]:
            ref = own[quarter]
            try:
                document = self._document(ref)
            except Exception:  # noqa: BLE001 - an unreadable release gives nothing
                continue
            texts[ref.accession] = [" ".join(" ".join(row) for row in document.tables[key].rows) if key in document.tables
                                    else document.blocks[key].text for key in document.order]
        records = []
        for kpi, (labels, gaps) in wanted.items():
            known = {quarter: float(v["value"]) for quarter, v in held[kpi].items() if v["validation_status"] == "verified"}
            found: dict[tuple[str, str], tuple[float, FilingRef]] = {}
            for _pass in range(3):  # a gap a table fills proves the next table
                before = len(found)
                for quarter in sorted(reach):
                    ref = own[quarter]
                    for text in texts.get(ref.accession, []):
                        for label in labels:
                            for row in quarter_table_rows(text, label):
                                figures = {(str(year), f"Q{number}"): value for (year, number), value in row.items()}
                                overlap = {q: known.get(q) or (found[q][0] if q in found else None) for q in figures}
                                overlap = {q: value for q, value in overlap.items() if value is not None}
                                scales = {next((scale for scale in (1, 1e3, 1e6, 1e9)
                                                if figures[q] and abs(value / (figures[q] * scale) - 1) < 1e-4), None)
                                          for q, value in overlap.items()}
                                if len(scales) != 1 or None in scales:
                                    continue
                                scale = scales.pop()
                                for gap in gaps & set(figures):
                                    if gap not in found or own.get(gap) is ref:
                                        found[gap] = (figures[gap] * scale, ref)
                if len(found) == before:
                    break
            template = max((v for v in held[kpi].values() if v["validation_status"] == "verified"), key=lambda v: v["period_end"])
            for quarter, (value, ref) in sorted(found.items()):
                end = own[quarter].period_end
                records.append({**template, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                "value": value, "method": "ai", "validation_status": "verified", "reconciliation_error_pct": None,
                                "source_accession": ref.accession, "source_url": ref.source_url, "filed_at": ref.filed.isoformat(),
                                "notes": [f"from the quarterly table of the release for {own_label(ref, own)}"],
                                "locator": {"quarter_table": True, "row": labels[0]}})
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records)} KPI values from the releases' quarterly tables")

    def _fill_quoted_rows(self, releases: list[FilingRef]) -> None:
        """A metric's missing quarters from rows its releases repeat in a fixed layout: TSMC's management report gives
        "Shipment (Kpcs, 12"-equiv. Wafer) 2,916 3,227 3,799" (the quarter, the one before, the year before). The layout
        is learned from the KPI's own readings, a column for a lag only when at least two readings agree with figures
        held and none contradicts. A gap is then filled from a reading's lagged column, or from its own quarter's release
        where that row's lagged columns equal figures held (each quarter filled vouches for the next)."""
        held: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
        for v in self.values.values():
            if v["group_kind"] == "metric" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                held.setdefault((v["group_key"], v["kpi_key"]), {})[(v["fiscal_year"], v["fiscal_period"])] = v
        index = lambda quarter: int(quarter[0]) * 4 + int(quarter[1][1]) - 1  # noqa: E731
        label_of = lambda number: (str(number // 4), f"Q{number % 4 + 1}")  # noqa: E731
        refs: dict[tuple[str, str], list[FilingRef]] = {}
        for ref in sorted(releases, key=lambda item: item.filed):
            quarter = self._quarter_of(ref.period_end) if ref.period_end else None
            if quarter and quarter[1] != "FY":
                refs.setdefault(quarter, []).append(ref)
        # Each quarter's end as stored figures give it: a quarter with no release of its own (TSMC's Q4 2022) still has one.
        ends = {(v["fiscal_year"], v["fiscal_period"]): str(v["period_end"])[:10] for v in self.values.values()
                if v["fiscal_period"] != "FY" and v.get("period_end")}
        ends.update({quarter: own[0].period_end.isoformat() for quarter, own in refs.items()})
        records = []
        loaded: dict[str, str] = {}

        def text_of(ref: FilingRef) -> str:
            if ref.accession not in loaded and len(loaded) < 250:
                try:
                    document = self._document(ref)
                    loaded[ref.accession] = " ".join(
                        " ".join(" ".join(row) for row in document.tables[key].rows) if key in document.tables
                        else document.blocks[key].text for key in document.order)
                except Exception:  # noqa: BLE001 - an unreadable release gives nothing
                    loaded[ref.accession] = ""
            return loaded.get(ref.accession, "")

        for kpi, rows in held.items():
            readings = []
            for quarter, v in rows.items():
                label = _row_label(v.get("locator") or {})
                quote = (v.get("locator") or {}).get("quote")
                if v["validation_status"] != "verified" or v["method"] != "ai" or not label or not quote:
                    continue
                numbers = next((found for found in row_numbers(quote, label) if found), None)
                scale = next((item for item in (1, 1e3, 1e6, 1e9) if numbers and numbers[0]
                              and abs(float(v["value"]) / (numbers[0] * item) - 1) < 1e-6), None) if numbers else None
                if scale:
                    readings.append((quarter, label, [n * scale for n in numbers], scale, v))
            known = {quarter: float(v["value"]) for quarter, v in rows.items() if v["validation_status"] == "verified"}
            layout = {}
            for column in (1, 2, 3):
                for lag in (1, 4):
                    agree = contradict = 0
                    for quarter, _label, numbers, _scale, _v in readings:
                        earlier = label_of(index(quarter) - lag)
                        if len(numbers) > column and earlier in known:
                            same = abs(numbers[column] - known[earlier]) <= abs(known[earlier]) * 1e-6
                            agree, contradict = agree + same, contradict + (not same)
                    if agree >= 2 and not contradict:
                        layout[column] = lag
            if not layout:
                continue
            first = min(index(quarter) for quarter in rows)
            lagged: dict[tuple[str, str], tuple[float, str, dict]] = {}
            for quarter, _label, numbers, _scale, reading in readings:
                source = {field: reading.get(field) for field in ("source_accession", "source_url", "filed_at")}
                for column, lag in layout.items():
                    earlier = label_of(index(quarter) - lag)
                    if len(numbers) > column and earlier not in rows and index(earlier) > first and earlier not in lagged:
                        lagged[earlier] = (numbers[column], f"the release for {quarter[1]} {quarter[0]}, its column a "
                                                           f"{'quarter' if lag == 1 else 'year'} before", source)
            labels = sorted({label for _quarter, label, _numbers, _scale, _v in readings})
            scale = readings[0][3] if readings else 1
            # A quarter's own release first; a later release's column where its own does not give the row.
            found: dict[tuple[str, str], tuple[float, str, dict]] = {}
            gaps = sorted((quarter for quarter in ends if index(quarter) > first and quarter not in rows), key=index)
            # A quarter held a quarter or a year after a gap: its release's earlier columns, when its own figure is the
            # one held (TSMC's Q4 2023 report gives Q4 2022 a year back).
            after = {label_of(index(gap) + lag) for gap in gaps for lag in set(layout.values())}
            for quarter in sorted((quarter for quarter in after if quarter in refs and quarter in known), key=index):
                for ref in refs[quarter][:12]:
                    for label in labels:
                        for numbers in row_numbers(text_of(ref), label):
                            if abs(numbers[0] * scale - known[quarter]) > abs(known[quarter]) * 1e-6:
                                continue
                            source = {"source_accession": ref.accession, "source_url": ref.source_url, "filed_at": ref.filed.isoformat()}
                            for column, lag in layout.items():
                                earlier = label_of(index(quarter) - lag)
                                if len(numbers) > column and earlier in gaps and earlier not in lagged:
                                    lagged[earlier] = (numbers[column] * scale, f"the release for {quarter[1]} {quarter[0]}, its "
                                                       f"column a {'quarter' if lag == 1 else 'year'} before", source)
            for quarter in gaps:
                for ref in refs.get(quarter, [])[:12]:
                    for label in labels:
                        for numbers in row_numbers(text_of(ref), label):
                            checks = [(numbers[column] * scale, label_of(index(quarter) - lag)) for column, lag in layout.items()
                                      if len(numbers) > column]
                            figure_of = lambda earlier: known.get(earlier, (found.get(earlier) or lagged.get(earlier) or (None,))[0])  # noqa: E731
                            proven = [(figure, earlier) for figure, earlier in checks if figure_of(earlier) is not None]
                            target = figure_of
                            if proven and all(abs(figure - target(earlier)) <= abs(target(earlier)) * 1e-6 for figure, earlier in proven):
                                source = {"source_accession": ref.accession, "source_url": ref.source_url,
                                          "filed_at": ref.filed.isoformat()}
                                found.setdefault(quarter, (numbers[0] * scale, f"the release for {quarter[1]} {quarter[0]}", source))
                                # Its own earlier columns fill gaps behind it too (Q1 2023's report gives Q4 2022).
                                for figure, earlier in checks:
                                    if earlier not in rows and index(earlier) > first and earlier not in lagged:
                                        lagged[earlier] = (figure, f"the release for {quarter[1]} {quarter[0]}, its column "
                                                                   f"for {earlier[1]} {earlier[0]}", source)
                    if quarter in found:
                        break
            found = {**lagged, **found}
            template = max((v for v in rows.values() if v["validation_status"] == "verified"), key=lambda v: v["period_end"])
            for quarter, (value, where, source) in sorted(found.items()):
                if quarter not in ends:
                    continue
                records.append({**template, **source, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": ends[quarter],
                                "value": value, "method": "ai", "validation_status": "verified", "reconciliation_error_pct": None,
                                "notes": [f"from {where}"], "locator": {"quoted_row": True, "row": labels[0]}})
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records)} KPI values from rows the releases repeat")

    def _kpi_template(self, group, kpi, key: str) -> dict:
        """The stored shape of a KPI no release has given yet."""
        return {"group_key": key, "group_label": group.label, "group_kind": group.kind, "kpi_label": kpi.label,
                "kpi_order": group.kpis.index(kpi), "group_order": AI_GROUP_ORDER + self._group_index(key), "unit": kpi.unit,
                "currency": self._default_currency() if kpi.unit == "currency" else None}

    def _group_index(self, key: str) -> int:
        return next((value["group_order"] - AI_GROUP_ORDER for value in self.values.values() if value["group_key"] == key), 0)

    def _pick_tags(self, unread: list[tuple], quarters, newest_first, facts_of) -> dict[str, object]:
        """The AI points to the tagged figure that is each KPI no release gives, in the latest report; code keeps a pick
        only when its unit fits the KPI and the same tag is in the report before (a KPI the company reports)."""
        latest = newest_first[0]
        lines = fact_lines(self._xbrl(quarters[latest][0]).facts.to_dataframe(), quarters[latest][1])
        ids = {f"F{index}": line for index, line in enumerate(lines)}
        document = "\n".join(f"[{id_}] {label} | {value:,.15g} | {unit}" for id_, (_tag, value, label, unit) in ids.items())
        wanted = "\n".join(f"- {kpi.key}: {kpi.label} ({kpi.unit})" for _group, kpi, _key in unread)
        try:
            answer = self._ask(ai.prompt(self.company, document, ai.TAG_TASK, f"KPIs:\n{wanted}"), "tags", ai.TagPicks,
                               thinking_first=True, model="flash")
        except (AiResponseError, ControlError) as error:
            self.result.errors.append(f"KPI tags: {error}"[:300])
            return {}
        picked = {}
        earlier = next((quarter for quarter in newest_first[1:] if facts_of(quarter)), None)
        for _group, kpi, _key in unread:
            line = ids.get(answer.picks.get(kpi.key) or "")
            if line is None or not unit_fits(kpi.unit, line[3]) or earlier is None or line[0] not in facts_of(earlier):
                continue
            picked[kpi.key] = line[0]
        if picked:
            self.log(f"{self.symbol}: tags picked for {', '.join(sorted(picked))}")
        return picked

    def _tagged_records(self, spec: Spec, quarters, newest_first, facts_of, opened: dict, unread: list[tuple],
                        picked: dict) -> list[dict]:
        records: list[dict] = []
        unread.clear()
        for group in spec.groups:
            # A breakdown's lines only take the report's exact figure (Adobe's customer groups, rounded to $10 million
            # in its releases, tagged to the dollar in its 10-Qs); a quarter is never filled one line at a time.
            split = group.kind == "revenue_breakdown"
            if group.kind != "metric" and not split:
                continue
            key = f"{AI_GROUP_PREFIX}{group.key}"
            for kpi in group.kpis:
                held = {(v["fiscal_year"], v["fiscal_period"]): v for v in self.values.values()
                        if v["group_key"] == key and v["kpi_key"] == kpi.key and v["fiscal_period"] != "FY"}
                readings = {quarter: float(v["value"]) for quarter, v in held.items()
                            if v["validation_status"] == "verified" and not (v.get("locator") or {}).get("tagged")}
                if split:
                    records += self._exact_lines(held, readings, quarters, facts_of)
                    continue
                missing = [quarter for quarter in newest_first if quarter not in held
                           or (held[quarter]["validation_status"] == "needs_review" and not held[quarter].get("owner_decision"))]
                if not missing:
                    continue
                # The tag: one found before (kept on the figures it filled), else learned from the releases' readings,
                # else, for a KPI the releases never state (Alphabet's revenue backlog), picked from the report's tags.
                tag = next((found for v in held.values() if (found := tag_from_locator(v.get("locator") or {}))), None)
                how = "tagged as in the quarters the release states it"
                if tag is None and kpi.key in picked:
                    tag, how = picked[kpi.key], "the releases do not state it"
                    if any(quarter in quarters and tag in facts_of(quarter) and not agrees(facts_of(quarter)[tag], value)
                           for quarter, value in readings.items()):
                        continue  # a release gave a different figure: not this tag
                elif tag is None and len(readings) >= 2:
                    known = [quarter for quarter in newest_first if quarter in readings and quarter in quarters][:6]
                    tag = learn_tag({quarter: readings[quarter] for quarter in known}, {quarter: facts_of(quarter) for quarter in known})
                elif tag is None:
                    unread.append((group, kpi, key))
                    continue
                if tag is None:
                    continue
                template = next((v for v in held.values() if v["validation_status"] == "verified"), None) or self._kpi_template(group, kpi, key)
                gaps = 0
                for quarter in newest_first:
                    if quarter not in missing:
                        # Never past a quarter whose report (when already opened) tags a different figure.
                        if quarter in readings and tag in opened.get(quarter, {}) and not agrees(opened[quarter][tag], readings[quarter]):
                            break
                        gaps = 0
                        # A release's rounded figure ("over $460 billion") gives way to the report's exact one.
                        fact = ((facts_of(quarter) if quarter in newest_first[:4] else opened.get(quarter, {})).get(tag)
                                if quarter in quarters else None)
                        reading = held.get(quarter)
                        if (fact is not None and quarter in readings and fact != readings[quarter] and reading
                                and not reading.get("owner_decision")):
                            ref, end = quarters[quarter]
                            records.append({**reading, "value": fact, "source_accession": ref.accession, "source_url": ref.source_url,
                                            "filed_at": ref.filed.isoformat(),
                                            "notes": [f"the {ref.form}'s exact figure; the release gave {(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                                            "locator": {"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                                        "instant": tag.instant}})
                        continue
                    fact = facts_of(quarter).get(tag)
                    if fact is None:
                        gaps += 1
                        if gaps >= 2:
                            break  # older reports do not tag it
                        continue
                    gaps = 0
                    ref, end = quarters[quarter]
                    records.append(self._record(
                        group_key=key, group_label=template["group_label"], group_kind=template["group_kind"],
                        group_order=template["group_order"], kpi_key=kpi.key, kpi_label=template["kpi_label"],
                        kpi_order=template["kpi_order"], unit=template["unit"], value=fact, currency=template.get("currency"),
                        method="ai", validation_status="verified", reconciliation_error_pct=None,
                        notes=[f"from the {ref.form}'s XBRL, {how}"],
                        ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=end,
                        locator={"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                 "instant": tag.instant},
                    ))
            if split:
                records += self._year_ago_lines(group, key, quarters, records)
                records += self._exact_periods(group, key, quarters, facts_of, records)
        return records

    def _exact_periods(self, group, key: str, quarters, facts_of, fresh: list[dict]) -> list[dict]:
        """A flagged breakdown quarter whose every line is its report's exact tagged figure (for a Q4, the 10-K's year less
        three settled quarters) is settled: KKR tags each of its asset management segment's five revenue lines to the
        thousand but not their total, so no total proves them. A quarter whose lines, with those its report tags and its
        reading missed or misread, then add up (each parent to its lines, the top lines to the total) takes them all.
        A line's tags are those that equal its readings in at least two quarters and never differ; a total the group
        lists must be its lines added up, to its rounding."""
        rows = {value_key(v): v for v in self.values.values() if v["group_key"] == key and v["validation_status"] != "rejected"
                and v["fiscal_period"] != "FY"}
        rows.update({value_key(v): v for v in fresh if v["group_key"] == key})
        by_period: dict[tuple[str, str], dict[str, dict]] = {}
        for v in rows.values():
            by_period.setdefault((v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
        waiting = sorted(quarter for quarter, lines in by_period.items() if quarter in quarters and any(
            v["validation_status"] == "needs_review" and not v.get("owner_decision") for v in lines.values()))
        if not waiting:
            return []
        tags = {}
        for kpi in group.kpis:
            if kpi.key == group.total_kpi:
                continue
            readings = {quarter: float(lines[kpi.key]["value"]) for quarter, lines in by_period.items()
                        if quarter in quarters and kpi.key in lines}
            # A tag that equals the readings in two quarters at least, and twice as often as it differs: one misread
            # quarter must not keep the others from being settled.
            agree: dict = {}
            differ: dict = {}
            for quarter in sorted(readings, reverse=True)[:12]:
                for tag, fact in facts_of(quarter).items():
                    if fact == readings[quarter]:
                        agree[tag] = agree.get(tag, 0) + 1
                    elif readings[quarter] and abs(fact / readings[quarter] - 1) < 0.2:  # the same series, another figure
                        differ[tag] = differ.get(tag, 0) + 1
            tags[kpi.key] = [tag for tag, count in agree.items() if count >= 2 and count >= 2 * differ.get(tag, 0)]
        records = []
        settled = {"another part of this breakdown needs review", "confirmed by review", "settled by the checks", *_UNANCHORED}
        parents = {kpi.key: kpi.parent for kpi in group.kpis if kpi.parent}

        def tagged(quarter, kpi: str) -> float | None:
            """The quarter's exact figure of a line: its learned tag in the report, or for a Q4 its 10-K's year less three
            verified quarters."""
            facts = facts_of(quarter)
            found = next((facts[tag] for tag in tags.get(kpi, []) if tag in facts), None)
            if found is not None or quarter[1] != "Q4":
                return found
            nine = [by_period.get((quarter[0], period), {}).get(kpi) for period in ("Q1", "Q2", "Q3")]
            if any(v is None or v["validation_status"] != "verified" for v in nine):
                return None
            ref, end = quarters[quarter]
            try:
                annual = quarter_facts(self._xbrl(ref).facts.to_dataframe(), end, (330, 400))
            except Exception:  # noqa: BLE001 - an unreadable report proves nothing
                return None
            return next((annual[tag] - sum(float(v["value"]) for v in nine) for tag in tags.get(kpi, []) if tag in annual), None)

        def whole(figures: dict[str, float], total: float | None) -> bool:
            """Each parent is its lines added up, and the lines not inside another add up to the total."""
            for parent in {parents[kpi] for kpi in figures if parents.get(kpi) in figures}:
                lines = sum(value for kpi, value in figures.items() if parents.get(kpi) == parent)
                if abs(figures[parent] - lines) > _rounding(figures[parent]):
                    return False
            top = sum(value for kpi, value in figures.items() if kpi != group.total_kpi and parents.get(kpi) not in figures)
            return total is None or abs(total - top) <= _rounding(total)

        # A quarter missing a line its report tags is completed (Revenue by Product at JPMorgan, read from a 3 MB
        # supplement whose segment pages repeat each row's name).
        listed = [kpi.key for kpi in group.kpis if kpi.key != group.total_kpi and tags.get(kpi.key)]
        waiting = sorted(set(waiting) | {quarter for quarter, lines in by_period.items() if quarter in quarters
                                         and quarter[1] != "FY" and len(lines) >= 2 and any(kpi not in lines for kpi in listed)})
        for quarter in waiting:
            lines = by_period[quarter]
            parts = {kpi: v for kpi, v in lines.items() if kpi != group.total_kpi}
            if len(parts) < 2:
                continue
            total_row = lines.get(group.total_kpi)
            total = float(total_row["value"]) if total_row else next(
                (float(t) for v in parts.values() if isinstance(t := (v.get("locator") or {}).get("total"), (int, float))), None)
            figures = {kpi: tagged(quarter, kpi) for kpi in parts}
            if any(value is None for value in figures.values()):
                continue
            added = {kpi: value for kpi in listed if kpi not in parts and (value := tagged(quarter, kpi)) is not None}
            changed = {kpi for kpi, value in figures.items() if value != float(parts[kpi]["value"])}
            if not whole({**figures, **added}, total):
                # The lines as read, when each is its report's figure and they add up (KKR's asset management lines).
                if changed or not whole(figures, total):
                    continue
                added = {}
            if not changed and not added and all(v["validation_status"] == "verified" for v in parts.values()):
                continue
            ref, end = quarters[quarter]
            for kpi, v in lines.items():
                notes = [note for note in v.get("notes") or [] if note not in settled and not _is_jump(note)]
                note = (f"the {ref.form}'s exact figure; the release gave {float(v['value']):,.15g}" if kpi in changed
                        else f"the {ref.form}'s exact figure; every line of the quarter is tagged")
                record = {**v, "validation_status": "verified", "notes": [*notes, note],
                          **({"value": figures[kpi]} if kpi in changed else {})}
                records.append(record)
                lines[kpi] = record  # the year's Q4 is worked out from it
            for kpi_key, value in added.items():
                kpi = next(item for item in group.kpis if item.key == kpi_key)
                shape = next((v for v in rows.values() if v["kpi_key"] == kpi_key), None) or self._kpi_template(group, kpi, key)
                record = self._record(
                    group_key=key, group_label=shape["group_label"], group_kind=shape["group_kind"],
                    group_order=shape["group_order"], kpi_key=kpi_key, kpi_label=shape["kpi_label"],
                    kpi_order=shape["kpi_order"], unit=shape["unit"], value=value, currency=shape.get("currency"),
                    method="ai", validation_status="verified", reconciliation_error_pct=None,
                    notes=[f"from the {ref.form}'s XBRL; the release's reading missed it"],
                    ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=end, locator={"tagged": True})
                records.append(record)
                lines[kpi_key] = record
        if records:
            self.log(f"{self.symbol}: {len(records)} breakdown values settled by their reports' exact tagged figures")
        return records

    def _year_ago_lines(self, group, key: str, quarters, fresh: list[dict]) -> list[dict]:
        """A breakdown's periods before its first release, from the reports' comparative columns: Adobe's FY2025 10-Qs
        tag FY2024's customer groups beside their own, its 10-K the two years before. A period is filled only when every
        line's tag (the one its own quarter's figure carries; a 10-K's, its Q3's) has a figure in it, and never over one
        held; a Q4 is then worked out from its year."""
        rows = {value_key(v): v for v in self.values.values() if v["group_key"] == key and v["validation_status"] != "rejected"}
        rows.update({value_key(v): v for v in fresh if v["group_key"] == key})
        by_period: dict[tuple[str, str], dict[str, dict]] = {}
        for v in rows.values():
            by_period.setdefault((v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
        records: list[dict] = []
        for quarter in sorted(by_period):
            if quarter not in quarters or quarter[1] == "FY":
                continue
            annual = quarter[1] == "Q4"
            source = by_period.get((quarter[0], "Q3") if annual else quarter) or {}
            tags = {kpi: tag for kpi, v in source.items() if (tag := tag_from_locator(v.get("locator") or {}))}
            # The lines the period has (a listed total no release gives, Adobe's, is not one), every one tagged.
            if len(tags) < 2 or set(tags) != set(source):
                continue
            ref, end = quarters[quarter]
            try:
                frame = self._xbrl(ref).facts.to_dataframe()
            except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                continue
            year = int(quarter[0])
            for _back in range(2 if annual else 1):
                found = year_ago_facts(frame, end, (330, 400) if annual else (80, 100))
                if found is None:
                    break
                end, facts = found
                year -= 1
                target = (str(year), "FY" if annual else quarter[1])
                if not all(tag in facts for tag in tags.values()):
                    break
                if target in by_period:
                    continue
                by_period[target] = {}
                for kpi, tag in tags.items():
                    record = {**source[kpi], "fiscal_year": target[0], "fiscal_period": target[1], "period_end": end.isoformat(),
                              "value": facts[tag], "method": "ai", "validation_status": "verified",
                              "reconciliation_error_pct": None, "source_accession": ref.accession, "source_url": ref.source_url,
                              "filed_at": ref.filed.isoformat(),
                              "notes": [f"the {ref.form}'s exact figure; its column for {target[1]} {target[0]}"],
                              "locator": {"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                          "instant": tag.instant, "year_ago": True}}
                    record.pop("owner_decision", None)
                    by_period[target][kpi] = record
                    records.append(record)
        if records:
            self.log(f"{self.symbol}: {len(records)} breakdown values from the reports' year-ago columns")
        return records

    def _exact_lines(self, held: dict, readings: dict, quarters, facts_of) -> list[dict]:
        """A release breakdown's rounded lines, each replaced by its report's exact tagged figure (Adobe's customer
        groups, to $10 million in its releases, to the dollar in its 10-Qs). Each quarter takes the one learned tag its
        own report carries, so a line the company retagged is followed across layouts. The line keeps its breakdown's
        total and the evidence that settled it."""
        known = [quarter for quarter in readings if quarter in quarters]
        tags = learn_tags({quarter: readings[quarter] for quarter in known}, {quarter: facts_of(quarter) for quarter in known})
        tags += [found for v in held.values() if (found := tag_from_locator(v.get("locator") or {})) and found not in tags]
        records = []
        for quarter in known:
            present = [tag for tag in tags if tag in facts_of(quarter)]
            reading = held[quarter]
            if len(present) != 1 or reading.get("owner_decision"):
                continue
            fact = facts_of(quarter)[present[0]]
            if fact == readings[quarter] or not agrees(fact, readings[quarter]):
                continue
            ref, _end = quarters[quarter]
            tag = present[0]
            records.append({**reading, "value": fact, "source_accession": ref.accession, "source_url": ref.source_url,
                            "filed_at": ref.filed.isoformat(),
                            "notes": [*(reading.get("notes") or []), f"the {ref.form}'s exact figure; the release gave "
                                      f"{(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                            "locator": {**(reading.get("locator") or {}), "tagged": True, "concept": tag.concept,
                                        "dimensions": dict(tag.dimensions), "instant": tag.instant}})
        # A Q4 the 10-K tags only for the year (Adobe's FY2025): the year's exact figure less the three exact quarters,
        # when exactly one of the year's figures leaves the release's rounded Q4.
        exact = {(v["fiscal_year"], v["fiscal_period"]): float(v["value"]) for v in [*held.values(), *records]
                 if (v.get("locator") or {}).get("tagged") and v["validation_status"] == "verified"}
        for quarter in known:
            year = quarter[0]
            nine = [exact.get((year, period)) for period in ("Q1", "Q2", "Q3")]
            if quarter[1] != "Q4" or quarter in exact or None in nine or held[quarter].get("owner_decision"):
                continue
            ref, end = quarters[quarter]
            try:
                annual = quarter_facts(self._xbrl(ref).facts.to_dataframe(), end, (330, 400))
            except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                continue
            fits = {fact - sum(nine) for fact in annual.values() if agrees(fact - sum(nine), readings[quarter])}
            if len(fits) != 1:
                continue
            reading, value = held[quarter], fits.pop()
            records.append({**reading, "value": value, "notes": [*(reading.get("notes") or []),
                            f"the {ref.form}'s exact year less its three quarters; the release gave "
                            f"{(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                            "locator": {**(reading.get("locator") or {}), "tagged": True}})
        return records

    # A recent listing's history: its first reports' year-ago figures and its IPO prospectus.

    def _prospectus(self, company, reports: list[FilingRef]) -> FilingRef | None:
        try:
            return ipo_prospectus(company, min((ref.filed for ref in reports), default=None))
        except Exception as error:  # noqa: BLE001 - the history is extra; the company is read without it
            self.log(f"{self.symbol}: prospectus not found ({type(error).__name__}: {error})"[:300])
            return None

    def _xbrl_groups(self) -> dict[str, list[dict]]:
        """Each XBRL breakdown's lines in its latest quarter (subtotal groups left out)."""
        latest: dict[str, tuple[str, str]] = {}
        rows = [value for value in self.values.values() if value["method"] == "xbrl" and value["validation_status"] == "verified"
                and value["fiscal_period"] != "FY" and not value["group_key"].endswith("_levels")
                and not value["group_key"].startswith(AI_GROUP_PREFIX) and value["group_key"] != INCOME_GROUP]
        for value in rows:
            latest[value["group_key"]] = max(latest.get(value["group_key"], ("", "")), (value["fiscal_year"], value["fiscal_period"]))
        groups: dict[str, list[dict]] = {}
        for value in rows:
            if (value["fiscal_year"], value["fiscal_period"]) == latest[value["group_key"]]:
                groups.setdefault(value["group_key"], []).append(value)
        return groups

    def _held(self, group: str, quarter: tuple[str, str]) -> bool:
        return any(value["group_key"] == group and (value["fiscal_year"], value["fiscal_period"]) == quarter
                   and value["validation_status"] != "rejected" for value in self.values.values())

    def _year_ago_quarters(self) -> None:
        """A company's first reports give each breakdown's year-ago quarter (SpaceX's Q2 2026 10-Q, Q2 2025): kept when
        every line has one and they add up to the year-ago total, for a quarter the company never reported."""
        records: list[dict] = []
        by_quarter: dict[tuple[str, str, str], list[dict]] = {}
        for value in self.values.values():
            # Years too: a first 10-K's comparative year lets the year before's Q4 be worked out from its quarters.
            if (value["method"] == "xbrl" and value["validation_status"] == "verified"
                    and not value["group_key"].endswith("_levels") and not value["group_key"].startswith(AI_GROUP_PREFIX)):
                by_quarter.setdefault((value["group_key"], value["fiscal_year"], value["fiscal_period"]), []).append(value)
        for (group, year, period), lines in by_quarter.items():
            before = (str(int(year) - 1), period)
            priors = [(line.get("locator") or {}).get("prior") for line in lines]
            total = next((t for line in lines if isinstance(t := (line.get("locator") or {}).get("prior_total"), (int, float))), None)
            if self._held(group, before) or total is None or not all(isinstance(prior, (int, float)) for prior in priors):
                continue
            if abs(sum(priors) - total) > abs(total) * 0.001:
                continue
            end = date.fromisoformat(str(lines[0]["period_end"])[:10])
            end = end.replace(year=end.year - 1, day=min(end.day, 28 if end.month == 2 else end.day))
            for line, prior in zip(lines, priors):
                records.append({**line, "fiscal_year": before[0], "period_end": end.isoformat(), "value": float(prior),
                                "reconciliation_error_pct": (sum(priors) - total) / total if total else None,
                                "notes": [f"the year-ago figure in the report for {period} {year}"],
                                "locator": {"total": total, "member": (line.get("locator") or {}).get("member"), "year_ago": True}})
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records)} year-ago values from the first reports' comparatives")

    def _store_prospectus_breakdowns(self, ref: FilingRef) -> None:
        """Each XBRL breakdown for every period the prospectus's tables give it, for periods the company never reported
        (see prospectus.py). Read once; the full years let the last pre-listing Q4 be worked out."""
        previous = self.filings.get(ref.accession) or {}
        if previous.get("status") in _DONE and _PROSPECTUS_READER in (previous.get("notes") or []):
            return
        try:
            document = self._document(ref)
            records: list[dict] = []
            renamed: dict[tuple[str, str], str] = {}  # (group, the prospectus's name) → the line it is today
            for group, lines in self._xbrl_groups().items():
                reference = next((t for line in lines if isinstance(t := (line.get("locator") or {}).get("total"), (int, float))), None)
                if not reference:
                    continue
                members = {line["kpi_key"]: line["kpi_label"] for line in lines}
                templates = {line["kpi_key"]: line for line in lines}
                for period, found in prospectus_breakdowns(document, members, float(reference)).items():
                    for member, label in found.rows.items():
                        if line_key(label) not in (member, line_key(templates[member]["kpi_label"])):
                            renamed[(group, line_key(label))] = member
                    try:
                        quarter = fiscal_label(period.end, self.profile.fiscal_year_end, annual=period.annual, year_offset=self.offset)
                    except Exception:  # noqa: BLE001 - a period off the company's calendar
                        continue
                    if self._held(group, quarter):
                        continue
                    for member, value in found.values.items():
                        template = templates[member]
                        records.append(self._record(
                            group_key=group, group_label=template["group_label"], group_kind=template["group_kind"],
                            group_order=template["group_order"], kpi_key=member, kpi_label=template["kpi_label"],
                            kpi_order=template["kpi_order"], unit="currency", value=value, currency=template.get("currency"),
                            method="xbrl", validation_status="verified",
                            reconciliation_error_pct=(sum(found.values.values()) - found.total) / found.total if found.total else None,
                            notes=["from the IPO prospectus"], ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1],
                            period_end=period.end, locator={"total": found.total, "member": (template.get("locator") or {}).get("member"),
                                                            "prospectus": True, "table": found.table, "row": found.rows.get(member)},
                        ))
            self._store_filing(ref, "processed" if records else "skipped", records=records,
                               notes=[_PROSPECTUS_READER, f"{len(records)} breakdown figures from the IPO prospectus"])
            self._join_prospectus_names(renamed)
        except Exception as error:  # noqa: BLE001 - the history is extra
            self.log(f"{self.symbol}: prospectus breakdowns not read ({type(error).__name__}: {error})"[:300])

    def _join_prospectus_names(self, renamed: dict[tuple[str, str], str]) -> None:
        """A line renamed after listing is one series under today's name: the prospectus's row the line was matched to
        (SpaceX's "AI", today "SI") names the reports' earlier key for it, when the two never share a quarter."""
        records = []
        for (group, old), new in renamed.items():
            rows = [v for v in self.values.values() if v["group_key"] == group and v["validation_status"] != "rejected"]
            before = [v for v in rows if v["kpi_key"] == old]
            current = [v for v in rows if v["kpi_key"] == new]
            if not before or not current or {(v["fiscal_year"], v["fiscal_period"]) for v in before} & {
                    (v["fiscal_year"], v["fiscal_period"]) for v in current}:
                continue
            for value in before:
                records.append({**value, "kpi_key": new, "kpi_label": current[0]["kpi_label"],
                                "notes": [*(value.get("notes") or []), f"named {value['kpi_label']} then"]})
                records.append({**value, "validation_status": "rejected", "notes": [f"continued as {current[0]['kpi_label']}"]})
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records) // 2} values joined to the line they were renamed to")

    def _fill_release_columns(self, releases: list[FilingRef]) -> None:
        """A recent listing's releases show earlier quarters beside their own (see release_columns): each KPI read from a
        release and each line of a breakdown its reports tag are taken from those columns for quarters held by nothing
        else, at the scale of the release's own figure. A breakdown's quarter must add up to the revenue another source
        gives for it (its year-ago comparative, the prospectus); one renamed line is the remainder, as in the prospectus."""
        records: list[dict] = []
        groups = self._xbrl_groups()
        for ref in releases:
            if ref.period_end is None:
                continue
            try:
                document = self._document(ref)
            except Exception:  # noqa: BLE001
                continue
            own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            grids = release_grids(document)
            rows = [(grid, label, cells, earlier_quarters(grid, cells, ref.period_end, additive=False),
                     next((amount(cell) for column, cell in zip(grid.columns, cells)
                           if column.months == 3 and abs((column.end - ref.period_end).days) <= 10), None))
                    for grid in grids for label, cells in grid.rows]
            records += self._release_column_kpis(ref, own, rows)
            records += self._release_column_lines(ref, own, rows, groups)
        if records:
            self._store(values=records)
            self.log(f"{self.symbol}: {len(records)} values from the earlier quarters the releases show")

    def _quarter_of(self, end: date) -> tuple[str, str] | None:
        try:
            return fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
        except Exception:  # noqa: BLE001
            return None

    def _release_column_kpis(self, ref: FilingRef, own: tuple[str, str], rows) -> list[dict]:
        records = []
        for value in [v for v in self.values.values() if v["source_accession"] == ref.accession and v["method"] == "ai"
                      and v["validation_status"] == "verified" and (v["fiscal_year"], v["fiscal_period"]) == own]:
            locator = value.get("locator") or {}
            label = None
            if locator.get("table") in self._document(ref).tables and isinstance(locator.get("row"), int):
                table = self._document(ref).tables[locator["table"]]
                label = clean(table.rows[locator["row"]][0]) if locator["row"] < len(table.rows) else None
            elif locator.get("quote"):
                label = re.split(r"\s\$?\(?[\d,.]+\)?(?:\s|$)", clean(locator["quote"]), maxsplit=1)[0]
            match = next(((grid, cells, earlier, mine) for grid, row_label, cells, earlier, mine in rows
                          if label and row_label == label and mine), None)
            if match is None:
                continue
            grid, cells, _, mine = match
            factor = float(value["value"]) / mine
            scale = next((item for item in (1, 1e3, 1e6, 1e9) if abs(factor / item - 1) < 0.01), None)
            if scale is None:
                continue
            additive = value["unit"] == "currency" and not re.search(r"\bper\b|average", label, re.I)
            for end, figure in earlier_quarters(grid, cells, ref.period_end, additive).items():
                quarter = self._quarter_of(end)
                if quarter is None or any(v["group_key"] == value["group_key"] and v["kpi_key"] == value["kpi_key"]
                                          and (v["fiscal_year"], v["fiscal_period"]) == quarter and v["validation_status"] != "rejected"
                                          for v in self.values.values()):
                    continue
                records.append({**value, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                "value": figure * scale, "notes": [f"an earlier column of the release for {own[1]} {own[0]}"],
                                "locator": {"release_column": end.isoformat(), "row": label, "source": grid.source}})
        return records

    def _release_column_lines(self, ref: FilingRef, own: tuple[str, str], rows, groups: dict[str, list[dict]]) -> list[dict]:
        records = []
        for group, lines in groups.items():
            stored = {v["kpi_key"]: v for v in self.values.values() if v["group_key"] == group and v["method"] == "xbrl"
                      and v["validation_status"] == "verified" and (v["fiscal_year"], v["fiscal_period"]) == own}
            members = {line["kpi_key"]: line for line in lines}
            found: dict[str, tuple] = {}
            for grid, label, cells, _, mine in rows:
                key = row_key(label)
                member = next((m for m, line in members.items() if key in (m, row_key(line["kpi_label"]))), None)
                if member and member not in found:
                    found[member] = (grid, label, cells, mine)
            # The scale: the release's own figure of a line against the report's.
            scale = next((item for member, (_g, _l, _c, mine) in found.items() if mine and member in stored
                          for item in (1, 1e3, 1e6, 1e9) if abs(float(stored[member]["value"]) / mine / item - 1) < 0.01), None)
            if scale is None:
                continue
            additive = {member: earlier_quarters(grid, cells, ref.period_end, True) for member, (grid, _l, cells, _m) in found.items()}
            ends = {end for quarters in additive.values() for end in quarters}
            for end in sorted(ends):
                quarter = self._quarter_of(end)
                if quarter is None or self._held(group, quarter):
                    continue
                total = next((float(t) for v in self.values.values() if (v["fiscal_year"], v["fiscal_period"]) == quarter
                              and v["validation_status"] == "verified" and v["method"] in ("xbrl", "derived")
                              and isinstance(t := (v.get("locator") or {}).get("total"), (int, float))), None)
                if total is None:
                    continue
                values = {member: quarters[end] * scale for member, quarters in additive.items() if end in quarters}
                missing = [member for member in members if member not in values]
                tolerance = 1.5 * scale * len(members)
                if len(missing) == 1:
                    # A renamed line (SpaceX's AI, SI by its next report): the one other row equal to the rest of revenue.
                    rest = total - sum(values.values())
                    candidates = {label: figure for grid, label, cells, _e, _m in rows if row_key(label) not in values
                                  for figure in [earlier_quarters(grid, cells, ref.period_end, True).get(end, float("nan")) * scale]
                                  if abs(figure - rest) <= tolerance}
                    if len(candidates) != 1:
                        continue
                    values[missing[0]] = next(iter(candidates.values()))
                elif missing:
                    continue
                if abs(sum(values.values()) - total) > tolerance:
                    continue
                for member, figure in values.items():
                    line = members[member]
                    records.append({**line, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                    "value": round(figure, 2), "method": CHART_METHOD, "source_accession": ref.accession,
                                    "source_url": ref.source_url, "filed_at": ref.filed.isoformat(), "reconciliation_error_pct": 0,
                                    "notes": [f"an earlier column of the release for {own[1]} {own[0]}"],
                                    "locator": {"release_table": "history", "total": total, "member": (line.get("locator") or {}).get("member")}})
        return records

    def _read_prospectus_quarters(self, ref: FilingRef, releases: list[FilingRef], spec: Spec, version: int) -> None:
        """The KPI list read from the prospectus, as from a release, for its latest interim quarter and the year before
        (SpaceX's Starlink subscribers, launches and mass to orbit for Q1 2026 and Q1 2025), when no release gave them.
        Each quarter is its own reading of the prospectus, recorded once."""
        try:
            document = self._document(ref)
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: prospectus not read ({type(error).__name__}: {error})"[:300])
            return
        covered = {ref.period_end for ref in releases if ref.period_end}
        for end in interim_quarters(document)[:2]:
            if any(abs((end - other).days) <= 10 for other in covered):
                continue
            year, period = fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
            quarter = replace(ref, accession=f"{ref.accession}-{year}{period}", period_end=end)
            if (self.filings.get(quarter.accession) or {}).get("status") in _DONE:
                continue
            labels = [kpi.label for group in spec.groups for kpi in group.kpis]
            self._documents[quarter.accession] = quarter_sections(document, labels, end)
            self._process_release(quarter, spec, version)

    def _series_for(self, axis: str) -> str | None:
        """The quarterly revenue series whose members a pie names: the group labelled "Revenue by <axis>" ("Wafer Revenue
        by Technology") among the values read from the quarters' reports (an annual report's XBRL series of that name
        is the year's, not this)."""
        groups = {value["group_key"] for value in self.values.values()
                  if value["validation_status"] == "verified" and value["unit"] == "currency"
                  and value["method"] != "xbrl" and value["fiscal_period"] != "FY"
                  and re.fullmatch(rf"(\w+ )?revenue by {axis}", str(value.get("group_label") or "").lower())}
        return groups.pop() if len(groups) == 1 else None

    def _release_revenue(self, ref: FilingRef) -> tuple[float, str | None] | None:
        """The quarter's net revenue as its release states it: its income statement, else its summary table."""
        document = self._document(ref)
        for statement in release_statements(document):
            if statement.statement == "income" and abs((statement.period_end - ref.period_end).days) <= 3:
                revenue = next((line["value"] for line in statement.lines if line["concept"] == "release_Revenues"), None)
                if revenue:
                    return float(revenue), statement.currency
        headline = release_headline(document, ref.period_end)
        revenue = next((line["value"] for line in headline.lines if line["concept"] == "release_Revenues"), None) if headline else None
        return (float(revenue), headline.currency) if revenue else None

    def _technology_share_records(self, pie: ChartBreakdown, group: str, ref: FilingRef, link: str) -> list[dict]:
        """A technology pie's shares of wafer revenue, under the series' members, as percentages. The release's own
        words ("2-nanometer accounted for 8% of total wafer revenue") must agree with the shares they name."""
        members: dict[str, dict] = {}
        for value in sorted(self.values.values(), key=lambda item: (item["fiscal_year"], item["fiscal_period"])):
            if value["group_key"] == group and value["validation_status"] == "verified" and value["unit"] == "currency":
                members[member_key(value["kpi_label"])] = value
        if any(member_key(name) not in members for name in pie.shares):
            return []
        try:
            text = re.sub(r"\s+", " ", " ".join(block.text for block in self._document(ref).blocks.values()))
        except Exception:  # noqa: BLE001 - without the release's words there is nothing to check against
            return []
        stated = {f"{node}nm": float(share) for node, share in
                  re.findall(r"(\d{1,2})-nanometer accounted for (\d{1,3})%", text)}
        if not stated or any(pie.shares.get(name) != share for name, share in stated.items()):
            return []
        return [self._record(
            group_key=group, group_label=members[member_key(name)]["group_label"],
            group_kind=members[member_key(name)]["group_kind"], group_order=members[member_key(name)]["group_order"],
            kpi_key=members[member_key(name)]["kpi_key"], kpi_label=members[member_key(name)]["kpi_label"],
            kpi_order=members[member_key(name)]["kpi_order"], unit="percent", value=share, currency=None,
            method=CHART_METHOD, validation_status="verified", reconciliation_error_pct=round(sum(pie.shares.values()) - 100, 2),
            notes=[f"the results slide's share of wafer revenue; its amount comes with the quarter's report"]
                  + (["16/20nm together on the slide"] if name == "16nm" else []),
            ref=ref, source_url=link, fiscal_year=pie.fiscal_year, fiscal_period=pie.fiscal_period, period_end=ref.period_end,
            locator={"image": link, "share": share, "share_of": "wafer revenue"},
        ) for name, share in pie.shares.items()]

    def _titled_pie_records(self, pie: ChartBreakdown, growth: dict[str, float], group: str,
                            base: tuple[float, str | None], ref: FilingRef, link: str) -> list[dict]:
        """Each platform: the quarter before's reported amount grown by the slide's rate, all scaled together to the
        quarter's net revenue. Kept only when that scaling is slight (the rates are rounded to points) and every
        platform's resulting share is the pie's to within its rounding."""
        quarter = (pie.fiscal_year, pie.fiscal_period)
        year, number = int(pie.fiscal_year), int(pie.fiscal_period[1])
        before = (str(year - 1), "Q4") if number == 1 else (str(year), f"Q{number - 1}")
        members: dict[str, dict] = {}
        previous: dict[str, float] = {}
        for value in sorted(self.values.values(), key=lambda item: (item["fiscal_year"], item["fiscal_period"])):
            if value["group_key"] != group or value["validation_status"] != "verified":
                continue
            members[member_key(value["kpi_label"])] = value
            if (value["fiscal_year"], value["fiscal_period"]) == before:
                previous[member_key(value["kpi_label"])] = float(value["value"])
        names = {member_key(name): name for name in pie.shares}
        if set(names) - set(members) or set(names) - set(previous) or {member_key(name) for name in growth} != set(names):
            return []
        amount, currency = base
        grown = {key: previous[key] * (1 + growth[names[key]] / 100) for key in names}
        factor = amount / sum(grown.values())
        if abs(factor - 1) > 0.015:
            return []
        values = {key: figure * factor for key, figure in grown.items()}
        if any(abs(values[key] / amount * 100 - pie.shares[names[key]]) > 0.6 for key in names):
            return []
        return [self._record(
            group_key=group, group_label=members[key]["group_label"], group_kind=members[key]["group_kind"],
            group_order=members[key]["group_order"], kpi_key=members[key]["kpi_key"], kpi_label=members[key]["kpi_label"],
            kpi_order=members[key]["kpi_order"], unit="currency", value=round(values[key], 2),
            currency=currency or members[key].get("currency"), method=CHART_METHOD, validation_status="verified",
            reconciliation_error_pct=round((factor - 1) * 100, 2),
            notes=[f"{before[1]} {before[0]} grown {growth[names[key]]:+g}% as the results slide shows, scaled to net revenue;"
                   f" the pie's share is {pie.shares[names[key]]:g}%"],
            ref=ref, source_url=link, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=ref.period_end,
            locator={"image": link, "share": pie.shares[names[key]], "growth": growth[names[key]], "total": amount},
        ) for key in names]

    def _store_chart_breakdowns(self, releases: list[FilingRef]) -> None:
        """What a release shows only as charts (ASML's slide: net system sales by technology, end-use and region as
        pies, and units sold by technology as bars), read from the slide's picture by a local OCR model, no AI service.

        A pie is a split of the amount it names, so it is stored as a revenue breakdown: each share of that quarter's
        net system sales, which the release's statements give. Units are stored as operating metrics and must add up
        to the quarter's reported units. A slide shows its quarter and the one before, so what one slide could not
        give is taken from the next. A slide that cannot be read never holds up the KPIs."""
        period_ends = {fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset): ref.period_end
                       for ref in releases if ref.period_end}
        chart_values = [value for value in self.values.values()
                        if value["method"] == CHART_METHOD and value["validation_status"] == "verified"]
        # Shares once stored as bare percentages are read again as amounts.
        have = {(value["group_key"], value["fiscal_year"], value["fiscal_period"]) for value in chart_values
                if value["unit"] != "percent"}
        groups = {key[0] for key in have}
        ordered = sorted(period_ends, key=period_ends.get)

        def complete(quarter: tuple[str, str]) -> bool:
            return len(groups) >= CHART_GROUPS and all((group, *quarter) in have for group in groups)

        # Pies a point off 100, waiting for a second slide to show the same shares.
        unconfirmed: dict[tuple[str, str, str], dict[str, float]] = {}
        for ref in sorted(releases, key=lambda item: item.filed):
            if ref.period_end is None:
                continue
            try:
                own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                before = ordered[ordered.index(own) - 1] if own in ordered and ordered.index(own) else None
                if complete(own) and (before is None or complete(before)):
                    continue  # this slide's two quarters are already stored whole
                records: list[dict] = []
                for exhibit in ref.exhibits[:6]:
                    raw = self._waiting(self._exhibit(exhibit).result)
                    for source, _title in breakdown_slides(raw or ""):
                        link = urllib.parse.urljoin(exhibit.url, source)
                        image = _fetch_image(link)
                        words = ocr_words(image)
                        # The bars' small figures are read again from an enlarged cut of the slide.
                        region = unit_region(words)
                        details = [ocr_words(image, region, scale) for scale in UNIT_SCALES] if region else []
                        pies = read_breakdowns(words)
                        if missing_pies(pies):
                            # A share misread at the slide's own size ("39%" as "%68") leaves its pie out; the slide
                            # read again enlarged gives the pies the first reading missed, under the same checks.
                            pies = [*pies, *fill_pies(pies, read_breakdowns(ocr_words(image, (0, 0), PIE_SCALE)))]
                        for chart in [*pies, *read_units(words, *details)]:
                            quarter = (chart.fiscal_year, chart.fiscal_period)
                            if quarter not in period_ends:
                                continue
                            rows = (self._unit_records(chart, ref, period_ends[quarter], link) if isinstance(chart, ChartUnits)
                                    else self._chart_records(chart, ref, period_ends[quarter], link))
                            if not rows or (rows[0]["group_key"], *quarter) in have:
                                continue
                            if isinstance(chart, ChartBreakdown) and not chart.exact:
                                key = (rows[0]["group_key"], *quarter)
                                if unconfirmed.get(key) != chart.shares:
                                    unconfirmed[key] = dict(chart.shares)
                                    continue
                            records.extend(rows)
                            have.add((rows[0]["group_key"], *quarter))
                            groups.add(rows[0]["group_key"])
                if records:
                    self._store(values=records)
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} chart figures stored ({len(records)} values)")
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: charts of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
        # A share left as a bare percentage (its quarter's amount is not known) is not a revenue breakdown: withdrawn.
        stale = [{**value, "validation_status": "rejected", "notes": ["a share without its amount; read again as a revenue breakdown"]}
                 for value in self.values.values()
                 if value["method"] == CHART_METHOD and value["unit"] == "percent" and value["validation_status"] != "rejected"
                 # A results-day share of an amount no release gives (TSMC's wafer revenue) is kept as a share.
                 and "share_of" not in (value.get("locator") or {})]
        for start in range(0, len(stale), 400):
            self._store(values=stale[start:start + 400])

    def _quarter_amount(self, subject: str, quarter: tuple[str, str], ref: FilingRef, period_end: date) -> tuple[float, str | None] | None:
        """The amount a pie splits ("Net system sales") for a quarter: the verified value read for it, else the line of
        that name in the release's own statements."""
        name = member_key(subject)
        for value in self.values.values():
            if ((value["fiscal_year"], value["fiscal_period"]) == quarter and value["unit"] == "currency"
                    and value["validation_status"] == "verified" and value["method"] != CHART_METHOD
                    and member_key(value["kpi_label"]) == name):
                return float(value["value"]), value.get("currency")
        for statement in release_statements(self._document(ref)):
            if statement.statement == "income" and abs((statement.period_end - period_end).days) <= 3:
                for line in statement.lines:
                    if not line["is_heading"] and member_key(line["label"]) == name and line["value"]:
                        return float(line["value"]), statement.currency
        return None

    def _chart_records(self, chart, ref: FilingRef, period_end: date, link: str) -> list[dict]:
        base = self._quarter_amount(chart.subject, (chart.fiscal_year, chart.fiscal_period), ref, period_end)
        if base is None:
            return []
        amount, currency = base
        axis_order = {"technology": 0, "end-use": 1, "region": 2}
        subject_key = re.sub(r"[^a-z0-9]+", "_", chart.subject.lower()).strip("_")
        axis_key = re.sub(r"[^a-z0-9]+", "_", chart.axis.lower()).strip("_")
        total = sum(chart.shares.values())
        ranked = sorted(chart.shares.items(), key=lambda item: -item[1])
        # The slide prints whole percentages, which need not add up to exactly 100: each share is taken of their sum,
        # so the parts add up to the quarter's amount.
        return [self._record(
            group_key=f"{AI_GROUP_PREFIX}{subject_key}_by_{axis_key}", group_label=f"{chart.subject} by {chart.axis}",
            group_kind="revenue_breakdown", group_order=60 + axis_order.get(chart.axis, 9),
            kpi_key=member_key(name)[:48] or "member", kpi_label=name, kpi_order=order, unit="currency",
            value=round(amount * share / total, 2), currency=currency or "USD", method=CHART_METHOD,
            validation_status="verified", reconciliation_error_pct=round(total - 100, 2),
            notes=[f"the slide's rounded share ({share:g}%) of {chart.subject.lower()}"], ref=ref, source_url=link,
            fiscal_year=chart.fiscal_year, fiscal_period=chart.fiscal_period, period_end=period_end,
            locator={"image": link, "share": share, "total": amount},
        ) for order, (name, share) in enumerate(ranked)]

    def _unit_records(self, chart, ref: FilingRef, period_end: date, link: str) -> list[dict]:
        """Units sold by technology. They must add up to the quarter's reported units (new and used systems) where
        those are known; a single count the picture did not give up is that total less the others."""
        quarter = (chart.fiscal_year, chart.fiscal_period)
        reported = [float(value["value"]) for value in self.values.values()
                    if (value["fiscal_year"], value["fiscal_period"]) == quarter and value["unit"] == "count"
                    and value["validation_status"] == "verified" and value["method"] != CHART_METHOD
                    and _SYSTEMS_SOLD.search(value["kpi_label"])]
        total = sum(reported) if len(reported) == 2 else None  # new and used
        # The release's own table of units a quarter is the better witness: it is read by rule, not by the AI.
        try:
            stated = next((units for end, units in reported_units(self._document(ref)).items()
                           if abs((end - period_end).days) <= 3), None)
        except Exception:  # noqa: BLE001 - a document that cannot be read leaves the other witness
            stated = None
        if stated is not None:
            total = float(stated)
        units = dict(chart.units)
        notes = ["read from the slide's chart"]
        if total is None and set(units) - chart.confirmed:
            return []  # nothing to check the bars against: every count must be one two readings agree on
        if chart.missing:
            rest = None if total is None else total - sum(units.values())
            # A fill takes whatever is left of the total, so it would hide a misread digit among the bars that were
            # read: each of those must be a count two readings of the picture agree on.
            if rest is None or rest < 0 or set(units) - chart.confirmed:
                return []
            units[chart.missing] = int(rest)
            notes = [f"{chart.missing}: the quarter's {total:g} units less the other technologies"]
        elif total is not None and sum(units.values()) != total:
            return []
        order = {"EUV": 0, "ArFi": 1, "ArF Dry": 2, "KrF": 3, "I-line": 4}
        return [self._record(
            group_key=f"{AI_GROUP_PREFIX}lithography_units_sold", group_label="Lithography units sold", group_kind="metric",
            group_order=70, kpi_key=f"{member_key(name)[:40]}units", kpi_label=f"{name} units sold",
            kpi_order=order.get(name, 9), unit="count", value=float(count), currency=None, method=CHART_METHOD,
            validation_status="verified", reconciliation_error_pct=None,
            notes=notes if name == chart.missing or not chart.missing else ["read from the slide's chart"],
            ref=ref, source_url=link, fiscal_year=chart.fiscal_year, fiscal_period=chart.fiscal_period,
            period_end=period_end, locator={"image": link, **({"total": total} if total is not None else {})},
        ) for name, count in units.items()]

    def _continuing_split(self, group, period_end: date):
        """Of two complete splits on one axis, the one the company's stored quarters of the past year use, so the year
        and its quarters line up (Broadcom's regions, not its 10-K's countries). A year with no such quarters follows
        the latest ones (Adobe's FY2016-18, before its 10-Qs gave regions); the finer split when nothing is stored."""
        if not group.alternative:
            return group
        window = (period_end - timedelta(days=370)).isoformat()
        # The quarters only: the year before may be on the 10-K's other split (Adobe's FY2018 countries).
        quarters = [v for v in self.values.values() if v["method"] == "xbrl" and v["group_key"] == group.key
                    and v["validation_status"] == "verified" and v["fiscal_period"] != "FY"]
        rows = [v for v in quarters if window <= v["period_end"] < period_end.isoformat()]
        if not rows and quarters:
            latest = max(v["period_end"] for v in quarters)
            rows = [v for v in quarters if v["period_end"] == latest]
        recent = {v["kpi_key"] for v in rows}
        # The same element under another label ("Total Americas" in Adobe's 10-K, "Americas" in its 10-Qs) is one line,
        # whichever quarter names it.
        # The 2018 taxonomy moved members such as AmericasMember from us-gaap to srt: one element either way.
        def standard(member: str | None) -> str | None:
            return re.sub(r"^(?:us-gaap|srt):", "std:", member) if member else None

        stored = {standard(member): v for v in sorted(quarters, key=lambda v: v["period_end"])
                  if (member := (v.get("locator") or {}).get("member"))}
        renamed = {key: stored[element] for key, _label, _value in group.alternative
                   if (element := standard(group.elements.get(key))) in stored and stored[element]["kpi_key"] != key}
        current = {key for key, _label, _value in group.members}
        other = {renamed[key]["kpi_key"] if key in renamed else key for key, _label, _value in group.alternative}
        if recent and len(other & recent) > len(current & recent):
            def rekey(values: dict) -> dict:
                return {renamed[key]["kpi_key"] if key in renamed else key: value for key, value in values.items()}
            members = [(renamed[key]["kpi_key"], renamed[key]["kpi_label"], value) if key in renamed else (key, label, value)
                       for key, label, value in group.alternative]
            return replace(group, members=members, alternative=group.members, elements=rekey(group.elements),
                           prior=rekey(group.prior), year_to_date=rekey(group.year_to_date),
                           prior_year_to_date=rekey(group.prior_year_to_date))
        return group

    def _curate(self, candidate: XbrlCandidate):
        """The reasoning reader picks the rows that make up revenue; the same rows are reused for later filings with the same rows."""
        cache_key = (candidate.key, ",".join(sorted(candidate.members))[:2000])  # stored under the same key
        if cache_key in self._curations:
            return candidate.group(self._curations[cache_key])  # The reasoning reader's choice, kept between runs
        rows = "\n".join(f"- {key}: {label} = {value:,.0f}" for key, (label, value) in candidate.members.items())
        data = (f"Breakdown: {candidate.label} ({candidate.concept})\nReported total revenue: {candidate.total:,.0f}\n"
                f"Rows (member key: label = amount):\n{rows}")
        try:
            answer = self._ask(ai.prompt(self.company, "(XBRL facts, listed below)", ai.CURATE_TASK, data),
                               "curate", ai.Curation, thinking_first=True, model="flash")
        except AiResponseError:
            return None
        self._curations[cache_key] = answer.members
        self.control.call("store", symbol=self.symbol, filings=[], values=[], curations=[
            {"group_key": candidate.key, "members_key": cache_key[1], "chosen": answer.members}])
        return candidate.group(answer.members)

    # Which furnished filings are earnings documents.

    def _is_earnings_document(self, ref: FilingRef) -> bool:
        if ref.confirmed:
            return True
        previous = self.filings.get(ref.accession)
        own = any(is_own_document(exhibit) for exhibit in ref.exhibits)
        if previous and not (own and _NOT_EARNINGS in (previous.get("notes") or [])
                             and _CLASSIFIED_PAST_COVER not in (previous.get("notes") or [])):
            return _NOT_EARNINGS not in (previous.get("notes") or [])
        try:
            answer = self._ask(ai.prompt(self.company, classification_excerpt(ref), ai.CLASSIFY_TASK),
                               "classify", ai.Classification, thinking_first=False, model="flash")
        except (AiResponseError, ControlError):
            return False  # nothing is recorded, so the next run classifies it again
        if answer.kind == "other":
            self._store_filing(ref, "skipped", notes=[_NOT_EARNINGS, *([_CLASSIFIED_PAST_COVER] if own else [])])
            return False
        return True

    # The KPI list: proposed once, then audited every new quarter.

    def _spec(self, row: dict | None, releases: list[FilingRef]) -> tuple[Spec | None, int, bool]:
        # A list the owner edited (Bedrock's KPI templates) is kept as edited, even on a forced run.
        if row and (not self.force or row.get("owner_edited_at")):
            stored = Spec.model_validate({"groups": row["groups"], "names": row.get("names") or {}})
            spec, version = self._capped(stored), int(row["version"])
            if not row.get("owner_edited_at"):
                # A group the extractor listed from a release read before its report (Microsoft's new segments, listed
                # an hour before the 10-Q tagged them) goes once the report's XBRL gives the same rows.
                cleaned = without_xbrl_repeats(spec, [] if self.profile.foreign else self._current_xbrl_members())
                if cleaned is not spec:
                    self._set_aside_dropped(spec, cleaned)
                    spec = cleaned
            if spec.model_dump() != stored.model_dump():
                # A stored list that breaks the rules is repaired once; its filings are then read with the new version.
                version += 1
                self._store_spec(spec, row.get("names") or {}, version,
                                 row.get("source_accession") or (releases[-1].accession if releases else "normalized"))
                self.log(f"{self.symbol}: KPI list v{version}: groups that repeat XBRL or cannot reconcile were changed")
            return spec, version, False
        if not releases:
            return None, 0, False
        latest = releases[-1]
        latest_quarter = [ref for ref in releases if ref.period_end == latest.period_end]
        breakdowns = self._xbrl_breakdowns()
        # Foreign issuers tag XBRL only once a year, so their quarterly breakdowns must still come from releases.
        covered = [] if self.profile.foreign else [
            f"{group['label']}: {', '.join(group['members'].values())}" for group in breakdowns.values()
        ]
        to_name = [
            f"- {key}: {group['label']} (" + "; ".join(f"{member} = {label}" for member, label in group["members"].items()) + ")"
            for key, group in breakdowns.items()
        ]
        try:
            document = self._document(*latest_quarter)
            text = self._prompt_document(document, None)
            # Reasoning first: the KPI list and names shape every later quarter.
            current = Spec.model_validate({"groups": row["groups"]}) if row else None
            spec = self._ask(ai.prompt(self.company, text, ai.PROPOSE_TASK, ai.propose_data(covered, to_name, current)),
                             "propose", Spec, thinking_first=True, model="flash")
        except Exception as error:  # noqa: BLE001
            self.result.errors.append(f"KPI list: {error}"[:300])
            return None, 0, False
        if current is not None:
            spec = keep_current_groups(spec, current)
        # A group the owner kept stays; one the extractor itself listed that repeats an XBRL breakdown (Amazon's product
        # and service sales, listed before XBRL tagged them as a view) goes.
        spec = without_xbrl_repeats(spec, [] if self.profile.foreign else self._current_xbrl_members(),
                                    keep=current if getattr(self, "_owner_edited", False) else None)
        self._set_aside_dropped(current, spec)
        spec = self._capped(spec)
        # Names may only rename breakdowns and members that exist.
        names = {
            key: {"label": name.label, "members": {m: l for m, l in name.members.items() if m in breakdowns[key]["members"]}}
            for key, name in spec.names.items() if key in breakdowns
        }
        version = (int(row["version"]) if row else 0) + 1
        self._store_spec(spec, names, version, latest.accession)
        self.names = names
        self.log(f"{self.symbol}: KPI list v{version} with {sum(len(g.kpis) for g in spec.groups)} KPIs")
        return spec, version, True

    def _set_aside_dropped(self, before: Spec | None, after: Spec) -> None:
        """Figures of a group the list no longer has because it repeats an XBRL breakdown are set aside, as the template
        page's save does, so the breakdown is not shown twice (once from XBRL, once as releases' leftover rows)."""
        if before is None:
            return
        covered = [] if self.profile.foreign else self._current_xbrl_members()
        kept = {group.key for group in after.groups}
        dropped = {f"{AI_GROUP_PREFIX}{group.key}" for group in before.groups
                   if group.key not in kept and repeats_xbrl(group, covered)}
        if not dropped:
            return
        aside = [{**value, "validation_status": "rejected", "notes": ["repeats a breakdown read from XBRL"]}
                 for value in self.values.values()
                 if value["group_key"] in dropped and value["method"] in ("ai", "derived") and value["validation_status"] != "rejected"]
        for start in range(0, len(aside), 400):
            self._store(values=aside[start:start + 400])
        if aside:
            self.log(f"{self.symbol}: {len(aside)} values of {', '.join(sorted(dropped))} set aside (repeats XBRL)")

    def _capped(self, spec: Spec) -> Spec:
        """normalize_spec, with any KPI the cap took off recorded for the run summary."""
        normalized = ai.normalize_spec(spec)
        kept = {(g.key, k.key) for g in normalized.groups for k in g.kpis}
        trimmed = [f"{g.key}.{k.key}" for g in spec.groups for k in g.kpis
                   if (g.key, k.key) not in kept and not any(k.key == other.key for other in
                                                              next((n.kpis for n in normalized.groups if n.kind == "metric"), []))]
        if trimmed:
            self.result.trimmed_kpis = sorted(set(self.result.trimmed_kpis) | set(trimmed))
            self.log(f"{self.symbol}: KPI cap left out {', '.join(trimmed[:10])}")
        return normalized

    def _maintain(self, spec: Spec, version: int, latest_quarter: list[FilingRef]) -> tuple[Spec, int] | None:
        """The reasoning reader audits the newest quarter against the list: KPIs the documents report that the list lacks, and KPIs the
        company stopped reporting. Additions only extend the list; existing keys never change meaning."""
        document = self._document(*latest_quarter)
        period_end = max(ref.period_end for ref in latest_quarter).isoformat()
        captured = sorted(
            f"- {v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']} ({v['group_label']} / {v['kpi_label']}) = {v['value']}"
            for v in self.values.values() if v["method"] == "ai" and abs(_days(v["period_end"], period_end)) <= 12
        )
        data = f"KPI list:\n{ai.kpi_lines(spec)}\n\nCaptured for this quarter:\n" + ("\n".join(captured) or "- nothing")
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.MAINTAIN_TASK, data),
                               "maintain", ai.Maintenance, thinking_first=True, model="flash")
            if getattr(self, "_owner_edited", False):
                # The owner decides what an edited list tracks: the audit may suggest additions, never retirements.
                answer = answer.model_copy(update={"retire": []})
            covered = [] if self.profile.foreign else self._current_xbrl_members()
            updated = apply_maintenance(spec, answer, covered)
            if not getattr(self, "_owner_edited", False):
                # The extractor's own repeats of an XBRL breakdown go, whether or not the audit changed anything else.
                cleaned = without_xbrl_repeats(updated or spec, covered)
                updated = cleaned if cleaned is not spec else updated
        except (AiResponseError, ControlError, ValueError) as error:
            self.result.errors.append(f"KPI list audit: {error}"[:300])
            return None
        if updated is None:
            return None
        updated = self._capped(updated)  # the audit's additions are kept to the cap like any list
        if updated.model_dump() == spec.model_dump():
            return None
        self._set_aside_dropped(spec, updated)
        self._store_spec(updated, self.names, version + 1, latest_quarter[-1].accession)
        self.log(f"{self.symbol}: KPI list v{version + 1} after audit ({sum(len(g.kpis) for g in updated.groups)} KPIs)")
        return updated, version + 1

    def _store_spec(self, spec: Spec, names: dict, version: int, accession: str) -> None:
        self.control.call("store", symbol=self.symbol, spec={
            "version": version, "groups": [group.model_dump() for group in spec.groups], "names": names,
            "sourceAccession": accession,
        })

    def _xbrl_breakdowns(self) -> dict[str, dict]:
        """Each XBRL breakdown's latest title and members (key → label)."""
        latest: dict[str, str] = {}
        for value in self.values.values():
            if value["method"] == "xbrl" and value["group_key"] != INCOME_GROUP and value["period_end"] >= latest.get(value["group_key"], ""):
                latest[value["group_key"]] = value["period_end"]
        breakdowns: dict[str, dict] = {}
        for value in sorted(self.values.values(), key=lambda item: item["kpi_order"]):
            if value["method"] == "xbrl" and value["period_end"] == latest.get(value["group_key"]):
                group = breakdowns.setdefault(value["group_key"], {"label": value["group_label"], "members": {}})
                group["members"][value["kpi_key"]] = value["kpi_label"]
        return breakdowns

    def _current_xbrl_members(self) -> list[set[str]]:
        """The member names of each XBRL breakdown the company still tags (its latest quarter within ~4 months of the
        newest XBRL figure): a list need not read these from releases. One the company stopped tagging (Meta's
        advertising and other revenue, to 2021) is not here, so its later quarters can come from releases."""
        newest = max((value["period_end"] for value in self.values.values() if value["method"] == "xbrl"), default=None)
        if newest is None:
            return []
        latest: dict[str, str] = {}
        for value in self.values.values():
            if value["method"] == "xbrl" and value["period_end"] >= latest.get(value["group_key"], ""):
                latest[value["group_key"]] = value["period_end"]
        return [{_identity_text(label) for label in group["members"].values()}
                for key, group in self._xbrl_breakdowns().items() if abs(_days(latest[key], newest)) <= 125]

    def _harmonize_labels(self) -> None:
        """One name per XBRL series across every period: the company's approved name, else its latest label."""
        latest: dict[tuple[str, str], dict] = {}
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            key = (value["group_key"], value["kpi_key"])
            if value["period_end"] >= latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        group_latest: dict[str, dict] = {}
        for (group_key, _kpi), value in latest.items():
            if value["period_end"] >= group_latest.get(group_key, {}).get("period_end", ""):
                group_latest[group_key] = value
        renamed = []
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            named = self.names.get(value["group_key"]) or {}
            label = named.get("label") or group_latest[value["group_key"]]["group_label"]
            member = ((named.get("members") or {}).get(value["kpi_key"])
                      or latest[(value["group_key"], value["kpi_key"])]["kpi_label"])
            if (label, member) != (value["group_label"], value["kpi_label"]):
                renamed.append({**value, "group_label": label, "kpi_label": member})
        for start in range(0, len(renamed), 400):
            self._store(values=renamed[start:start + 400])

    # Earnings documents: KPIs the AI points to.

    def _process_release(self, ref: FilingRef, spec: Spec, version: int) -> bool:
        previous = self.filings.get(ref.accession)
        if not self.force and previous and previous.get("spec_version") == version and (
                previous["status"] in _DONE or previous.get("attempts", 0) >= _MAX_ATTEMPTS) and not release_reread_due(previous):
            return False
        if not spec.groups:
            self._store_filing(ref, "skipped", spec_version=version, notes=["no KPIs tracked for this company"])
            return True
        if ref.period_end != self._newest_release:
            spec = self._readable(spec)  # the newest quarter always reads every group: a fixed bug lets it verify
        try:
            expected = ref.period_end
            fiscal_year, fiscal_period = fiscal_label(expected, self.profile.fiscal_year_end, year_offset=self.offset)
            document = self._document(ref)
            period_hint = f"quarter ended about {expected} (fiscal {fiscal_period} {fiscal_year})"

            def text(subset: Spec | None = None) -> str:  # built only when the AI reads: a replayed quarter costs none
                return ai.prompt(self.company, self._prompt_document(document, spec), ai.LOCATE_TASK,
                                 ai.locate_data(period_hint, subset or spec, self._hints(expected)))

            read, problems, located = self._read(text, document, spec, expected, version)
            how = _READ_BY_REPLAY if getattr(self._local, "replayed", False) else _READ_BY_AI
            period_problem = check_period(located, expected)
            period_end = expected if period_problem else date.fromisoformat(located.period_end[:10])
            # A document counts as annual only on evidence: its breakdown adds up to the XBRL full year, or its
            # column says so. A total far above the quarter before is only a reason to look again.
            if not located.annual and (self._reports_full_year(read, period_end) or _annual_columns(read)):
                located.annual = True
                problems.append("breakdown reports the full year: stored as the fiscal year")
            elif not located.annual and fiscal_period == "Q4" and self._far_above_quarters(read, period_end):
                for item in read:
                    if item.group.kind == "revenue_breakdown":
                        item.status = "needs_review"
                        item.notes.append("total far above the quarter before: check the period")
            if located.annual:
                # An annual report: its values are the full year, and Q4 is derived from them.
                fiscal_year, fiscal_period = fiscal_label(period_end, self.profile.fiscal_year_end, annual=True,
                                                          year_offset=self.offset)
            validate_groups(read, self._previous(period_end, annual=located.annual))
            self._flag_partial_tables(read, period_end, document)
            self._explain_jumps(read, document, spec)
            records = self._release_records(read, spec, ref, document, fiscal_year, fiscal_period, period_end, period_problem)
            verified = sum(1 for record in records if record["validation_status"] == "verified")
            status = "processed" if verified else "needs_review" if records else "skipped"
            unreported = _unreported(spec, read)
            notes = [how, _RELEASE_READER, *([_NOT_REPORTED + ", ".join(unreported)] if unreported else []), *problems[:20]]
            self._store_filing(ref, status, records=records, spec_version=version, period_end=period_end,
                               fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes)
        except Exception as error:  # noqa: BLE001
            self._fail_filing(ref, error, spec_version=version)
        return True

    def _readable(self, spec: Spec) -> Spec:
        """The list without groups that have never verified in at least eight quarters read (JPMorgan's income-statement
        lines listed as a breakdown): reading them again would only flag them again, at a reasoning read each quarter. Decided
        once per run (see _process_releases), before re-reading a filing could withdraw their history. Used for older
        quarters only: the newest is read whole every run, so a group that failed for an extractor bug verifies again
        once the bug is fixed, and is read in full from then on."""
        dead = self._dead if self._dead is not None else self._never_verified()
        if not dead or all(group.key in dead for group in spec.groups):
            return spec
        return Spec.model_validate({"groups": [g.model_dump() for g in spec.groups if g.key not in dead],
                                    "names": spec.names})

    def _never_verified(self) -> set[str]:
        periods: dict[str, set[tuple[str, str]]] = {}
        verified: set[str] = set()
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                group = v["group_key"].removeprefix(AI_GROUP_PREFIX)
                periods.setdefault(group, set()).add((v["fiscal_year"], v["fiscal_period"]))
                if v["validation_status"] == "verified":
                    verified.add(group)
        return {group for group, seen in periods.items() if len(seen) >= DEAD_GROUP_QUARTERS and group not in verified}

    def _waiting(self, wait):
        """Lets other readers work while this one waits on the AI or a download, when it holds the state lock."""
        if not getattr(self._local, "holding", False):
            return wait()
        self._state.release()
        try:
            return wait()
        finally:
            self._state.acquire()

    def _process_releases(self, refs: list[FilingRef], spec: Spec, version: int) -> list[FilingRef]:
        """Reads releases in four chains, one per fiscal quarter (every Q1 oldest to newest, every Q2, ...), the chains
        side by side. Each read then has the same quarter a year earlier already read in this run: the reference for
        where each KPI sits, and the year-ago figure its column must match. Readings stored by an earlier run for a
        filing this run is still to re-read are never a reference. Afterwards the quarters just read are checked against
        each other in date order, as reading them one by one would have."""
        before = {key for key, value in self.values.items() if value["method"] == "ai"}
        self._dead = self._never_verified()
        pending = [ref for ref in refs if self.force or self._pending(ref) or
                   (self.filings.get(ref.accession) or {}).get("spec_version") != version]
        done: set[str] = set()

        def read(ref: FilingRef) -> None:
            if self._process_release(ref, spec, version):
                done.add(ref.accession)

        def chain(quarter_refs: list[FilingRef]) -> None:
            for ref in quarter_refs:
                with self._state:
                    self._local.holding = True
                    self._local.chained = True  # history lookups see this chain's own earlier quarters only
                    try:
                        read(ref)
                    finally:
                        self._local.holding = False
                        self._local.chained = False

        if len(pending) <= 2:
            for ref in refs:
                read(ref)
        else:
            chains: dict[int, list[FilingRef]] = {}
            for ref in sorted(refs, key=lambda ref: ref.period_end or date.min):
                chains.setdefault(_chain_of(ref.period_end or date.min), []).append(ref)
            with ThreadPoolExecutor(max_workers=RELEASE_READERS) as pool:
                list(pool.map(chain, chains.values()))
            self._check_read_series({key for key, value in self.values.items() if value["method"] == "ai"} - before
                                    | {key for key in before if self.values[key].get("source_accession") in done}, refs)
        return [ref for ref in refs if ref.accession in done]

    def _check_read_series(self, keys: set[tuple], refs: list[FilingRef] = ()) -> None:
        """The jump checks of quarters read side by side, run again in date order: each value against the quarter before
        it (a share against its last share, an amount against three times or a third of its last), and an amount equal
        to its own figure a year earlier (the tell of a prior-year column). A breakdown or mix is flagged whole."""
        series: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] == "verified" and v["fiscal_period"] != "FY":
                series.setdefault((v["group_key"], v["kpi_key"]), []).append(v)
        flagged: dict[tuple, str] = {}
        for rows in series.values():
            rows.sort(key=lambda v: v["period_end"])
            for previous, current in zip(rows, rows[1:]):
                if value_key(current) not in keys or _days(previous["period_end"], current["period_end"]) > 120:
                    continue
                before, now = float(previous["value"]), float(current["value"])
                small = count_jump(current["unit"], before, now)
                if current["unit"] == "percent":
                    jump = abs(now - before) > MAX_MIX_JUMP and f"moved {now - before:+.1f} points in a quarter"
                elif small is not None:
                    jump = small or None
                else:
                    jump = before and now / before > 0 and not 1 / MAX_RATIO_JUMP <= now / before <= MAX_RATIO_JUMP \
                        and f"changed {now / before:.1f}x in a quarter"
                if jump and not self._immaterial(current):
                    flagged[value_key(current)] = jump
                elif current["unit"] == "currency" and _significant(now) and _same(now, before):
                    flagged[value_key(current)] = "the same figure as the quarter before: check the column"
            by_end = {v["period_end"][:7]: v for v in rows}
            for current in rows:
                year_ago = by_end.get(f"{int(current['period_end'][:4]) - 1}{current['period_end'][4:7]}")
                if (value_key(current) in keys and year_ago and current["unit"] == "currency"
                        and _significant(float(current["value"])) and _same(float(current["value"]), float(year_ago["value"]))):
                    flagged[value_key(current)] = "the same figure as a year earlier: check the column"
        groups = {(k[0], k[2], k[3]) for k in flagged if self.values[k]["group_kind"] != "metric"}
        changed = []
        for key, value in self.values.items():
            group_period = (key[0], key[2], key[3])
            if key in flagged or (group_period in groups and value["method"] == "ai" and value["validation_status"] == "verified"):
                note = flagged.get(key, "another part of this breakdown needs review")
                changed.append({**value, "validation_status": "needs_review", "notes": [*(value.get("notes") or []), note]})
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])
        self._explain_stored({key for key, note in flagged.items() if _is_jump(note)}, refs)

    def _explain_stored(self, keys: set[tuple], refs: list[FilingRef]) -> None:
        """Jumps the date-order check found, given the reading-time treatment: the reasoning reader is asked whether each is a real
        change, and one backed by a quote found in the document stands (its breakdown's other parts are released by
        _release_held_breakdowns). One reasoning call per release with such a jump."""
        by_accession = {ref.accession: ref for ref in refs}
        jumps: dict[str, list[dict]] = {}
        for key in keys:
            value = self.values.get(key)
            if value and value.get("source_accession") in by_accession:
                jumps.setdefault(value["source_accession"], []).append(value)
        def explain(item: tuple[str, list[dict]]):
            accession, rows = item
            try:
                document = self._document(by_accession[accession])
                lines = "\n".join(
                    f"- {v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']} ({v['group_label']} / {v['kpi_label']}): "
                    f"now {float(v['value']):g}; {'; '.join(n for n in v.get('notes') or [] if _is_jump(n))}" for v in rows)
                answer = self._ask(ai.prompt(self.company, self._prompt_document(document, None), ai.EXPLAIN_TASK, lines),
                                   "explain", ai.Explanations, thinking_first=True, model="flash")
                return rows, document, answer
            except Exception:  # noqa: BLE001 - unexplained, they stay flagged
                return rows, None, None

        cleared = []
        with ThreadPoolExecutor(max_workers=RELEASE_READERS) as pool:  # one reasoning call per release, side by side
            answers = list(pool.map(explain, jumps.items()))
        for rows, document, answer in answers:
            if answer is None:
                continue
            haystack = _document_text(document)
            for v in rows:
                verdict = answer.items.get(f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}")
                quote = clean(verdict.quote or "") if verdict else ""
                if verdict and verdict.legitimate and len(quote) >= 20 and quote in haystack:
                    cleared.append({**v, "validation_status": "verified",
                                    "notes": [n for n in v.get("notes") or [] if not _is_jump(n)] + [f"change explained: {quote[:200]}"]})
        for start in range(0, len(cleared), 400):
            self._store(values=cleared[start:start + 400])

    def _immaterial(self, value: dict) -> bool:
        """A breakdown row under 1% of its total, whose swings say nothing (as validate_groups treats it)."""
        total = (value.get("locator") or {}).get("total")
        return value["group_kind"] == "revenue_breakdown" and bool(total) and abs(float(value["value"])) < 0.01 * abs(float(total))

    def _carry_owner_additions(self, spec: Spec, version: int, previous: dict | None, releases: list[FilingRef]) -> None:
        """When the owner only added KPIs (a new list version that keeps every KPI of the one before), older quarters
        keep their readings and move to the new version; only the last four quarters are read for the additions, as
        the audit's additions are. A run reaching past a refresh's window (the page's Read filings, ten years) reads the
        additions in every quarter it covers: a KPI's history (Meta's Facebook DAUs, reported to 2023)."""
        if not previous or int(previous.get("version") or 0) >= version or not releases or self.quarters > REFRESH_QUARTERS:
            return
        try:
            before = Spec.model_validate({"groups": previous.get("groups") or [], "names": {}})
        except ValueError:
            return
        if not only_adds(before, spec):
            return
        latest_end = max(ref.period_end for ref in releases if ref.period_end) if any(ref.period_end for ref in releases) else None
        if latest_end is None:
            return
        cutoff = latest_end - timedelta(days=370)
        carried = 0
        for ref in releases:
            filing = self.filings.get(ref.accession) or {}
            if ref.period_end and ref.period_end < cutoff and filing.get("spec_version") == int(previous["version"]):
                self._carry_to_version(ref, version)
                carried += 1
        if carried:
            self.log(f"{self.symbol}: owner added KPIs; {carried} older filings kept, the last four quarters read again")

    def _carry_to_version(self, ref: FilingRef, version: int) -> None:
        filing = self.filings.get(ref.accession)
        if not filing or filing.get("status") not in _DONE:
            return
        # A complete checkpoint: the request check requires the filing's identity, not only what changed.
        carried = {"accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(),
                   "document_role": ref.role, "source_url": ref.source_url, "period_end": filing.get("period_end"),
                   "spec_version": version, "status": filing["status"], "attempts": max(1, filing.get("attempts") or 1),
                   "value_count": filing.get("value_count", 0), "notes": filing.get("notes") or []}
        self._store(filings=[carried])
        self.filings[ref.accession] = {**filing, "spec_version": version}

    def _release_records(self, read: list[ReadValue], spec: Spec, ref: FilingRef, document: Document, fiscal_year: str,
                         fiscal_period: str, period_end: date, period_problem: str | None) -> list[dict]:
        # A breakdown's total row is its reconciliation target, not a segment of its own.
        def is_total(item: ReadValue) -> bool:
            return item.is_total or item.kpi.key == item.group.total_kpi

        totals = {item.group.key: item.value for item in read if item.group.kind == "revenue_breakdown" and is_total(item)}
        parts_sum: dict[str, float] = {}
        for item in read:
            if item.group.key in totals and not is_total(item):
                parts_sum[item.group.key] = parts_sum.get(item.group.key, 0.0) + item.value
        # A breakdown or mix is published whole or not at all: one flagged part flags them all, so the last complete
        # reading stays in place (the store never lets an unchecked row replace a verified one).
        open_groups = {item.group.key for item in read if item.group.kind != "metric" and not is_total(item)
                       and item.status != "verified"}
        records = []
        for item in read:
            if is_total(item):
                continue
            notes, status = list(item.notes), item.status
            if item.group.key in open_groups and status == "verified":
                status, notes = "needs_review", [*notes, "another part of this breakdown needs review"]
            if period_problem:
                status, notes = "needs_review", [*notes, period_problem]
            total = totals.get(item.group.key)
            records.append(self._record(
                group_key=AI_GROUP_PREFIX + item.group.key, group_label=item.group.label,
                group_kind=item.group.kind, group_order=AI_GROUP_ORDER + spec.groups.index(item.group),
                kpi_key=item.kpi.key, kpi_label=item.kpi.label,
                kpi_order=item.group.kpis.index(item.kpi) if item.kpi in item.group.kpis else 100 + int(item.locator.get("row", 0)),
                unit=item.kpi.unit, value=item.value, currency=item.currency, method="ai",
                validation_status=status, reconciliation_error_pct=(parts_sum[item.group.key] - total) / total if total else None,
                notes=notes, ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period, period_end=period_end,
                locator={**item.locator, "total": total} if total else item.locator, source_url=document.source_url,
            ))
        return records

    def _read(self, text, document: Document, spec: Spec, expected: date, version: int | None = None):
        """Code first: each group read the way it was last quarter (or in the nearest quarter read), standing only on
        the checks a Flash reading must pass; this holds across KPI-list versions, so a list change sends only new or
        changed groups to the AI. The rest goes to Flash. A group Flash's reading confirms stands alone; the reasoning reader reads the
        others independently, values stand where the two agree or where the checks settle them, and the reasoning reader reviews the
        rest."""
        currency = self._default_currency()
        self._local.replayed = False
        replayed, replay_located, done = self._replay_groups(document, spec, expected, currency)
        if done and all(group.key in done for group in spec.groups):
            self._local.replayed = True
            self.result.replayed_reads += 1
            return replayed, [], replay_located
        open_spec = spec if not done else Spec.model_validate(
            {"groups": [g.model_dump() for g in spec.groups if g.key not in done], "names": {}})
        read, problems, located = self._read_ai(text, document, open_spec, expected, version, currency, partial=bool(done))
        return replayed + read, problems, located

    def _read_ai(self, text, document: Document, spec: Spec, expected: date, version: int | None, currency: str | None,
                 partial: bool = False):
        def build(subset: Spec | None = None) -> str:
            if not callable(text):
                return text
            try:
                return text(subset) if subset is not None else text()
            except TypeError:
                return text()  # a prompt that does not take a part of the list
        full_text = build(spec if partial else None)
        flash, flash_read, flash_problems = self._locate(full_text, document, spec, currency, model="flash", thinking_first=False)
        if self._flash_is_enough(flash, flash_read, spec, expected, document) or (
                not flash.tables and not flash.values and self._kind_reports_nothing(document, expected, version)):
            self.result.flash_only_reads += 1
            return flash_read, [f"flash {problem}" for problem in flash_problems], flash
        # Groups Flash's reading confirms on their own stand; the reasoning reader reads only the rest of the list.
        confirmed = self._confirmed_groups(flash, flash_read, spec, expected, document)
        open_groups = [group for group in spec.groups if group.key not in confirmed]
        kept = [item for item in flash_read if item.group.key in confirmed]
        if not open_groups:
            self.result.flash_only_reads += 1
            return flash_read, [f"flash {problem}" for problem in flash_problems], flash
        subset = spec if not confirmed else Spec.model_validate({"groups": [g.model_dump() for g in open_groups],
                                                                 "names": {}})
        self.result.second_reads += 1
        # The second reader: Flash, reasoning first, reading the document on its own.
        pro, pro_read, pro_problems = self._locate(full_text if subset is spec else build(subset), document, subset,
                                                   currency, model="flash", thinking_first=True)
        open_keys = {group.key for group in open_groups}
        by_flash = {f"{item.group.key}.{item.kpi.key}": item for item in flash_read if item.group.key in open_keys}
        by_pro = {f"{item.group.key}.{item.kpi.key}": item for item in pro_read}
        agreed: list[ReadValue] = list(kept)
        disputed: dict[str, tuple[ReadValue | None, ReadValue | None]] = {}
        for key in [*by_flash, *[key for key in by_pro if key not in by_flash]]:
            a, b = by_flash.get(key), by_pro.get(key)
            if a and b and _same(a.value, b.value):
                agreed.append(a)
            else:
                disputed[key] = (a, b)
        problems = [f"flash {problem}" for problem in flash_problems] + [f"pro {problem}" for problem in pro_problems]
        self._settle_by_checks(agreed, disputed, expected)
        if disputed:
            lines = [f"- {key}: A = {describe(document, a) if a else 'not reported'}; "
                     f"B = {describe(document, b) if b else 'not reported'}" for key, (a, b) in disputed.items()]
            # The reasoning reader reviews only the tables and passages the two readings point to, with the opening for the period.
            pointed = {str(item.locator.get("table") or item.locator.get("block")) for pair in disputed.values()
                       for item in pair if item}
            review = self._ask(ai.prompt(self.company, document.render_ids(pointed, MAX_DOCUMENT_CHARS, opening=3),
                                         ai.REVIEW_TASK, "Disputed KPIs:\n" + "\n".join(lines)),
                               "review", ai.Review, thinking_first=True, model="flash")
            for key, (a, b) in disputed.items():
                chosen = {"A": a, "B": b}.get(review.choices.get(key, "none"))
                if chosen is None:
                    problems.append(f"{key}: readings disagree and review kept neither")
                    continue
                chosen.notes.append("confirmed by review")
                agreed.append(chosen)
        located = flash if flash.period_end else pro
        return agreed, problems, located

    def _confirmed_groups(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                          document: Document) -> set[str]:
        """Groups a single reading settles on its own: the reading's period holds, the group passes every check, no value
        repeats an earlier period's number, nothing found last quarter is missing, and each value is where it was last
        quarter or matches the year-ago figure in its own row. None when the reading's period itself is in doubt."""
        if check_period(located, expected) or located.annual or self._reports_full_year(read, expected):
            return set()
        earlier = self._earlier_values(expected)
        last = self._last_locators(expected, _source_template(document.source_url))
        target = (expected - timedelta(days=365)).isoformat()
        stored = {f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}": float(v["value"])
                  for v in self.values.values() if v["validation_status"] == "verified" and v["method"] in ("ai", "derived")
                  and v["fiscal_period"] != "FY" and abs(_days(v["period_end"], target)) <= 12}
        confirmed = set()
        for group in spec.groups:
            items = [item for item in read if item.group.key == group.key]
            parts = [item for item in items if not (item.is_total or item.kpi.key == group.total_kpi)]
            if not parts:
                continue
            keys = {f"{item.group.key}.{item.kpi.key}" for item in items}
            whole = any(item.locator.get("whole_table") for item in items)
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis}
            # Something this group had in the reference quarter is missing now (a key the current list no longer has,
            # from a reading under an older list, is not missing: it is not asked for).
            if any(key.split(".", 1)[0] == group.key and key not in keys and (whole or key in listed) for key in last):
                continue
            if any(item.kpi.unit in ("currency", "count") and any(_same(item.value, value) for value in
                   earlier.get(f"{group.key}.{item.kpi.key}", [])) for item in parts):
                continue
            trial = copy.deepcopy(items)
            validate_groups(trial, self._previous(expected))
            if not all(item.status == "verified" for item in trial):
                continue
            if all(self._same_place_as_last(item, expected, document)
                   or self._year_ago_cell_matches(item, document, stored.get(f"{group.key}.{item.kpi.key}")) for item in parts):
                confirmed.add(group.key)
        return confirmed

    def _settle_by_checks(self, agreed: list[ReadValue], disputed: dict[str, tuple], expected: date) -> None:
        """Two readings of a group that disagree, settled without a reasoning review only on structural evidence: one adds up
        (a breakdown to its total, a mix to 100%) and holds no number repeated from an earlier period, while the other
        does not add up or repeats an earlier period's number. A jump alone never decides: last quarter's figure read
        from another column passes the jump check precisely because it did not move, so a jump goes to the review and
        the explanation step as before. A single metric one reader found and the other did not goes to the review."""
        earlier = self._earlier_values(expected)
        for group in {key.split(".", 1)[0] for key in disputed}:
            pairs = {key: pair for key, pair in disputed.items() if key.split(".", 1)[0] == group}
            sample = next(item for pair in pairs.values() for item in pair if item)
            if sample.group.kind == "metric":
                continue  # nothing to add up against
            base = [item for item in agreed if item.group.key == group]
            sound, broken = [], []
            for side in (0, 1):
                rows = base + [pair[side] for pair in pairs.values() if pair[side]]
                trial = copy.deepcopy(rows)
                validate_groups(trial, {})  # sums and totals only: no previous quarter, so no jump can decide
                adds_up = bool(rows) and all(item.status == "verified" for item in trial)
                repeats = any(item.kpi.unit in ("currency", "count") and any(
                    _same(item.value, value) for value in earlier.get(f"{group}.{item.kpi.key}", []))
                    for pair in pairs.values() if (item := pair[side]))
                sound.append(adds_up and not repeats)
                broken.append(not adds_up or repeats)
            if sound[0] and broken[1] or sound[1] and broken[0]:
                winner = 0 if sound[0] else 1
                for key, pair in pairs.items():
                    if pair[winner]:
                        pair[winner].notes.append("settled by the checks")
                        agreed.append(pair[winner])
                    del disputed[key]

    def _locate(self, text: str, document: Document, spec: Spec, currency: str | None, model: str, thinking_first: bool):
        """One reader's answer read back by code, with one retry when its pointers do not hold (a text block cited as a
        table, a table id the document does not have), the reader told which and why."""
        located = self._ask(text, "locate", Located, thinking_first=thinking_first, model=model)
        read, problems = read_values(document, spec, located, currency)
        invalid = [problem for problem in problems if _INVALID_POINTER.search(problem)]
        if invalid:
            retry_text = text + "\n\nYour previous answer had pointers that do not hold:\n" + "\n".join(
                f"- {_explain_pointer(problem, document)}" for problem in invalid[:20])
            located = self._ask(retry_text, "locate", Located, thinking_first=thinking_first, model=model)
            read, problems = read_values(document, spec, located, currency)
        return located, read, problems

    def _replay_groups(self, document: Document, spec: Spec, expected: date, currency: str | None):
        """Each group read again by code from where it was found in the nearest quarter of the same kind of document,
        kept only when it passes every check a Flash-only reading must (its period, its sums, no jumps, nothing found
        before missing, every value where it was). A KPI the list has but the nearest reading did not find is looked
        for by the AI unless that reading's AI read noted it as not reported. Returns the kept groups' values."""
        source = _source_template(document.source_url)
        last = self._last_locators(expected, source)
        if not last:
            return [], None, set()
        accession = next((value.get("source_accession") for key, value in self._ai_history(expected, source).items()
                          if key in last), None)
        notes = (self.filings.get(accession or "") or {}).get("notes") or []
        reported = next((note for note in notes if note.startswith(_NOT_REPORTED)), _NOT_REPORTED)
        not_reported = set(reported.removeprefix(_NOT_REPORTED).split(", ")) if _READ_BY_AI in notes else set()
        # In the newest release a KPI never read anywhere is always looked for: the audit listed it from that release, so
        # another quarter not reporting it says nothing of this one (Amazon Now's cities, listed from the Q2 2026
        # release, were taken as absent there because Q1 lacked them). Older quarters keep the every-other-quarter rule.
        if expected == getattr(self, "_newest_release", None):
            ever = {f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}" for value in self.values.values()
                    if value["method"] == "ai" and value["validation_status"] != "rejected"}
            not_reported = {key for key in not_reported if key in ever}
        items: list[ReadValue] = []
        kept_located, done = None, set()
        for group in spec.groups:
            mine = {key: locator for key, locator in last.items() if key.split(".", 1)[0] == group.key}
            if not mine:
                continue
            whole = any(locator.get("whole_table") for locator in mine.values())
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis if kpi.key != group.total_kpi}
            if not whole and (listed - mine.keys()) - not_reported:
                continue  # a KPI the list has and nobody has looked for in this kind of document yet
            located = replay(document, mine, expected)
            if located is None:
                continue
            read, problems = read_values(document, spec, located, currency)
            read = [item for item in read if item.group.key == group.key]
            if problems or not read or self._far_above_quarters(read, expected):
                continue
            if group.key not in self._confirmed_groups(located, read, spec, expected, document):
                continue
            items += read
            done.add(group.key)
            kept_located = kept_located or located
        return items, kept_located, done

    def _kind_reports_nothing(self, document: Document, expected: date, version: int | None) -> bool:
        """Whether both models already read this kind of document for this KPI list and found nothing in it (TSMC's
        earnings release once its amounts come from the quarterly report). Flash pointing at nothing again then stands;
        a pointer that does not hold (a partial table) still goes to the reasoning reader."""
        if version is None:
            return False
        if _OWN_6K_DOCUMENT.search(document.source_url.rsplit("/", 1)[-1]):
            # A 6-K's own document is named for the form, not its content (TSMC's "d582139d6k.htm" is a results release
            # one quarter and the financial statements the next): its name says nothing of what an earlier one held.
            return False
        source = _source_template(document.source_url)
        return any(filing.get("spec_version") == version and filing["status"] == "skipped"
                   and _READ_BY_AI in (filing.get("notes") or []) and filing.get("value_count", 0) == 0
                   and _source_template(filing.get("source_url") or "") == source
                   and (filing.get("period_end") or "9999") < expected.isoformat()
                   for filing in self.filings.values())

    def _flash_is_enough(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                         document: Document) -> bool:
        """Whether one reading stands without a second: its period checks, no value repeats an earlier period's number,
        every check passes, and either every KPI sits where it sat last quarter or the year-ago column confirms it."""
        if check_period(located, expected) or located.annual or self._reports_full_year(read, expected):
            return False
        earlier = self._earlier_values(expected)
        for item in read:
            key = f"{item.group.key}.{item.kpi.key}"
            if item.is_total or item.kpi.key == item.group.total_kpi:
                continue  # totals are not stored; the parts must still add up to them
            if item.kpi.unit in ("currency", "count") and any(_same(item.value, value) for value in earlier.get(key, [])):
                return False  # the same number as an earlier period: likely a prior-period column
        trial = copy.deepcopy(read)
        validate_groups(trial, self._previous(expected))
        if not all(item.status == "verified" for item in trial):
            return False
        return self._where_it_was(read, expected, document) or self._year_ago_confirms(read, document, expected)

    def _same_place_as_last(self, item: ReadValue, expected: date, document: Document) -> bool:
        last = self._last_locators(expected, _source_template(document.source_url))
        key = f"{item.group.key}.{item.kpi.key}"
        return key in last and _same_place(item.locator, last[key])

    def _where_it_was(self, read: list[ReadValue], expected: date, document: Document) -> bool:
        last = self._last_locators(expected, _source_template(document.source_url))
        if not last:
            return False  # a company's first quarters, and a new kind of document, are not placed yet
        keys = {f"{item.group.key}.{item.kpi.key}" for item in read}
        if any(key not in keys for key in last):
            return False  # something found last quarter is missing now
        return all(item.is_total or item.kpi.key == item.group.total_kpi
                   or (f"{item.group.key}.{item.kpi.key}" in last
                       and _same_place(item.locator, last[f"{item.group.key}.{item.kpi.key}"]))
                   for item in read)

    def _year_ago_confirms(self, read: list[ReadValue], document: Document, expected: date) -> bool:
        """Every value is accounted for: confirmed by the release's own year-ago column (read by code from the same row,
        in a column headed for the same length of period, equal to what is stored for that quarter last year) or found
        where it was last quarter; at least two by the year-ago column. A value with neither (a new KPI, a quoted
        figure) sends the reading to the reasoning reader. Costs no AI."""
        target = (expected - timedelta(days=365)).isoformat()
        stored = {f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}": float(v["value"])
                  for v in self.values.values() if v["validation_status"] == "verified" and v["method"] in ("ai", "derived")
                  and v["fiscal_period"] != "FY" and abs(_days(v["period_end"], target)) <= 12}
        confirmed = 0
        for item in read:
            if item.is_total or item.kpi.key == item.group.total_kpi:
                continue
            if self._year_ago_cell_matches(item, document, stored.get(f"{item.group.key}.{item.kpi.key}")):
                confirmed += 1
            elif not self._same_place_as_last(item, expected, document):
                return False
        return confirmed >= 2

    @staticmethod
    def _year_ago_cell_matches(item: ReadValue, document: Document, before: float | None) -> bool:
        return before is not None and any(abs(cell - before) <= abs(before) * 1e-4
                                          for cell in _year_ago_cells(document, item.locator, item.value))

    def _flag_partial_tables(self, read: list[ReadValue], expected: date, document: Document) -> None:
        """A breakdown read with fewer rows than in the reference quarter and a total under half of that quarter's is
        most likely part of its table, with a subtotal taken for the total (TSMC's 2nm and 3nm under "advanced
        technologies"): its parts add up, so only the comparison shows it. Flagged whole."""
        history = self._ai_history(expected, _source_template(document.source_url))
        for group in {item.group.key for item in read if item.group.kind == "revenue_breakdown"}:
            items = [item for item in read if item.group.key == group]
            total = next((item.value for item in items if item.is_total or item.kpi.key == item.group.total_kpi), None)
            parts = [item for item in items if not (item.is_total or item.kpi.key == item.group.total_kpi)]
            reference = [value for key, value in history.items() if key.split(".", 1)[0] == group]
            if not reference or total is None:
                continue
            newest = max(value["period_end"] for value in reference)
            before = [value for value in reference if value["period_end"] == newest]
            before_total = next((float(v["locator"]["total"]) for v in before if (v.get("locator") or {}).get("total")), None)
            if before_total and len(parts) < len(before) and total < 0.5 * before_total:
                for item in items:
                    item.status = "needs_review"
                    item.notes.append("fewer rows and under half the total of the quarter compared: likely part of the table")

    def _explain_jumps(self, read: list[ReadValue], document: Document, spec: Spec) -> None:
        """Values flagged only for a sharp change get a reading by the reasoning reader; a real change backed by a quote stands."""
        flagged = [item for item in read if item.status == "needs_review"
                   and item.notes and all(_is_jump(note) for note in item.notes if note != "confirmed by review")]
        if not flagged:
            return
        lines = "\n".join(f"- {item.group.key}.{item.kpi.key} ({item.group.label} / {item.kpi.label}): now {item.value:g}"
                          f"; {'; '.join(item.notes)}" for item in flagged)
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.EXPLAIN_TASK, lines),
                               "explain", ai.Explanations, thinking_first=True, model="flash")
        except (AiResponseError, ControlError):
            return  # the flagged values stay needs_review
        haystack = _document_text(document)
        for item in flagged:
            verdict = answer.items.get(f"{item.group.key}.{item.kpi.key}")
            quote = clean(verdict.quote or "") if verdict else ""
            if verdict and verdict.legitimate and len(quote) >= 20 and quote in haystack:
                item.status = "verified"
                item.notes = [*item.notes, f"change explained: {quote[:200]}"]

    # Documents.

    def _ask(self, text: str, purpose: str, schema, thinking_first: bool, model: str = "flash"):
        last_error = None
        for thinking in (thinking_first, True):
            answer = self._waiting(lambda: self.control.ai(self.symbol, purpose, ai.SYSTEM, text, thinking, model))
            self.result.ai_calls += 1
            if not answer:
                raise AiResponseError("AI is unavailable")
            try:
                return ai.parse(answer, schema)
            except AiResponseError as error:
                last_error = error
        raise last_error

    def _prompt_document(self, document: Document, spec: Spec | None) -> str:
        """The document as the AI reads it: whole when it fits, else the sections Flash picks from its outline."""
        key = str(id(document))
        if key in self._prompt_documents:
            return self._prompt_documents[key]
        if document.fits(MAX_DOCUMENT_CHARS):
            text = document.render()
        elif spec is not None and (found := self._sections_with_every_kpi(document, spec)):
            text = document.render_ids(found, MAX_DOCUMENT_CHARS)  # code found every KPI: no selection call
        else:
            wanted = ai.kpi_lines(spec) if spec else "(no list yet: revenue breakdowns and operating metrics)"
            try:
                answer = self._ask(ai.prompt(self.company, document.outline(), ai.SELECT_TASK, f"KPIs:\n{wanted}"),
                                   "select", ai.Selection, thinking_first=False, model="flash")
                ids = {item for item in answer.ids if item in document.tables or item in document.blocks}
            except AiResponseError:
                ids = set()
            # Sections that name a listed KPI go in whatever Flash picks: its outline is cut off in a long report, and
            # TSMC's 2018 statements hold the technology table past that cut (with no 2nm–7nm rows to name them all).
            named = self._sections_naming_kpis(document, spec) if spec is not None else set()
            ids |= named
            text = document.render_ids(ids, MAX_DOCUMENT_CHARS, first=named) if ids else document.render_for_prompt(MAX_DOCUMENT_CHARS)
        self._prompt_documents[key] = text
        return text

    def _sections_naming_kpis(self, document: Document, spec: Spec) -> set[str]:
        """The tables with a row named for a listed KPI (by its label, or the row label it had last quarter)."""
        hints = {key: (value.get("locator") or {}).get("row_label") for key, value in self._ai_history(date.max).items()}
        names = {_identity_text(kpi.label) for group in spec.groups for kpi in group.kpis if kpi.key != group.total_kpi}
        names |= {_identity_text(label) for label in hints.values() if label}
        names.discard("")
        return {key for key, table in document.tables.items() if any(_identity_text(row[0]) in names for row in table.rows if row)}

    def _sections_with_every_kpi(self, document: Document, spec: Spec) -> set[str] | None:
        """The tables and passages naming each listed KPI (by its label, or the row label it had last quarter), when
        every KPI is found and together they fit; None sends the choice to Flash."""
        hints = {key: (value.get("locator") or {}).get("row_label") for key, value in self._ai_history(date.max).items()}
        found: set[str] = set()
        for group in spec.groups:
            for kpi in group.kpis:
                if kpi.key == group.total_kpi:
                    continue
                names = {_identity_text(kpi.label)} | ({_identity_text(hints[f"{group.key}.{kpi.key}"])}
                                                       if hints.get(f"{group.key}.{kpi.key}") else set())
                names.discard("")
                hits = {key for key, table in document.tables.items()
                        if any(_identity_text(row[0]) in names for row in table.rows if row)}
                hits |= {key for key, block in document.blocks.items()
                         if any(name and name in _identity_text(block.text) for name in names if len(name) >= 8)}
                if not hits:
                    return None
                found |= hits
        rendered = sum(len((document.tables.get(key) or document.blocks[key]).render()) for key in found)
        return found if rendered <= MAX_DOCUMENT_CHARS * 0.9 else None

    def _document(self, *refs: FilingRef) -> Document:
        """The exhibits of one or more filings as one document; each exhibit's ids carry their own letter prefix."""
        key = "+".join(ref.accession for ref in refs)
        if key not in self._documents:
            combined: Document | None = None
            exhibits = [exhibit for ref in refs for exhibit in ref.exhibits[:6]][:26]
            for index, exhibit in enumerate(exhibits):
                raw = self._waiting(self._exhibit(exhibit).result)
                if raw is None:
                    continue
                part = parse_document(raw, exhibit.url, prefix=f"{string.ascii_uppercase[index]}_")
                if combined is None:
                    combined = part
                else:
                    combined.extend(part)
            if combined is None:
                raise ValueError("no readable exhibit")
            self._documents[key] = combined
        return self._documents[key]

    # History used by the checks and hints.

    def _ai_history(self, before: date, source: str | None = None) -> dict[str, dict]:
        """The latest AI value of each KPI before a date (optionally only from one kind of document); when none is
        before it (an older quarter read after newer ones), the nearest reading after it, which plays the same part:
        where each KPI was found in the nearest quarter."""
        latest: dict[str, dict] = {}
        later: dict[str, dict] = {}
        cutoff = before.isoformat()
        chained = getattr(self._local, "chained", False)
        for value in list(self.values.values()):
            if value["method"] != "ai" or abs(_days(value["period_end"], cutoff)) <= 20:
                continue
            if chained and (value["period_end"] > cutoff or not _same_chain(value["period_end"], before)):
                continue  # in a chain: only its own earlier quarters, which are always read first
            if source is not None and _source_template(value.get("source_url") or "") != source:
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] < cutoff:
                if value["period_end"] > latest.get(key, {}).get("period_end", ""):
                    latest[key] = value
            elif value["period_end"] < later.get(key, {}).get("period_end", "9999"):
                later[key] = value
        return latest or later

    def _hints(self, before: date) -> dict[str, str]:
        return {key: hint for key, value in self._ai_history(before).items() if (hint := locator_hint(value.get("locator")))}

    def _last_locators(self, before: date, source: str) -> dict[str, dict]:
        """Where each KPI was found the previous quarter in the same kind of document (TSMC's earnings release and its
        financial report carry different KPIs, and are compared only with their own kind)."""
        history = {key: value for key, value in self._ai_history(before, source).items()}
        if not history:
            return {}
        newest = max(value["period_end"] for value in history.values())
        return {key: value["locator"] for key, value in history.items()
                if value["period_end"] == newest and value.get("locator") and value["validation_status"] == "verified"}

    def _far_above_quarters(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown's total is several times the quarter just before it (an annual report read as a
        quarter). Only the adjacent quarter counts: a fast grower's Q4 is far above its quarters of years ago."""
        for item in read:
            if item.group.kind != "revenue_breakdown" or not (item.is_total or item.kpi.key == item.group.total_kpi):
                continue
            before = self._quarter_total_before(AI_GROUP_PREFIX + item.group.key, period_end.isoformat())
            if before and item.value >= 2.5 * before:
                return True
        return False

    def _quarter_total_before(self, group_key: str, period_end: str) -> float | None:
        """The reported total of the group's latest quarter within 120 days before period_end. In a reading chain, where
        that quarter may not be read yet, the quarter's own revenue in XBRL (an annual total is about four times it)."""
        if getattr(self._local, "chained", False):
            return next((float(v["locator"]["total"]) for v in list(self.values.values())
                         if v["method"] == "xbrl" and v["fiscal_period"] != "FY" and (v.get("locator") or {}).get("total")
                         and abs(_days(v["period_end"], period_end)) <= 12), None)
        candidates = [v for v in self.values.values()
                      if v["group_key"] == group_key and v["fiscal_period"] in ("Q1", "Q2", "Q3", "Q4")
                      and v["validation_status"] != "rejected" and (v.get("locator") or {}).get("total")
                      and 0 < _days(period_end, v["period_end"]) <= 120]
        latest = max(candidates, key=lambda v: v["period_end"], default=None)
        return float(latest["locator"]["total"]) if latest else None

    def _refile_annuals(self) -> None:
        """Repairs full-year figures stored as Q4 before these checks existed: every AI value from that filing moves
        to FY, and the Q4 rows are rejected so Q4 is derived as FY − Q1 − Q2 − Q3. A reading from a column headed for
        three months stays the quarter it is (Apple's FY2019 Q4 release: its product-type table was moved to FY with
        the release's yearly readings, and Q4 came out at −$110.8 billion); one moved before is moved back."""
        self._unfile_quarters()
        annual_totals = {v["period_end"]: float(v["locator"]["total"]) for v in self.values.values()
                         if v["method"] == "xbrl" and v["fiscal_period"] == "FY" and (v.get("locator") or {}).get("total")}
        misfiled = set()
        for v in self.values.values():
            total = (v.get("locator") or {}).get("total")
            if v["method"] != "ai" or v["fiscal_period"] != "Q4" or v["validation_status"] == "rejected" or not total:
                continue
            total = float(total)
            annual = next((a for end, a in annual_totals.items() if abs(_days(end, v["period_end"])) <= 12), None)
            header = str((v.get("locator") or {}).get("header") or "")
            if (annual and abs(total - annual) <= abs(annual) * 0.01) or _annual_header(header):
                misfiled.add((v["source_accession"], v["fiscal_year"]))
        if not misfiled:
            return
        moved = []
        for v in list(self.values.values()):
            if v["method"] == "ai" and v["fiscal_period"] == "Q4" and (v["source_accession"], v["fiscal_year"]) in misfiled \
                    and not _quarter_column(v):
                # A jump flagged while the year was mistaken for a quarter says nothing about the year.
                notes = [note for note in (v.get("notes") or []) if not _is_jump(note)]
                status = "verified" if v["validation_status"] == "needs_review" and not notes else v["validation_status"]
                moved.append({**v, "fiscal_period": "FY", "validation_status": status,
                              "notes": [*notes, "full-year figures, re-filed from Q4"]})
                moved.append({**v, "validation_status": "rejected", "notes": ["full-year figures filed as Q4; see FY"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])
        self.log(f"{self.symbol}: re-filed {len(misfiled)} annual report(s) stored as Q4")

    def _unfile_quarters(self) -> None:
        """A quarter's figures moved to FY with their release's yearly readings go back to Q4, the year's derived rows
        rejected so the year is worked out again."""
        back = []
        for v in list(self.values.values()):
            if v["method"] != "ai" or v["fiscal_period"] != "FY" or v["validation_status"] == "rejected" \
                    or "full-year figures, re-filed from Q4" not in (v.get("notes") or []) or not _quarter_column(v):
                continue
            notes = [note for note in v.get("notes") or [] if note != "full-year figures, re-filed from Q4"]
            back.append({**v, "fiscal_period": "Q4", "validation_status": "verified", "notes": notes})
            back.append({**v, "validation_status": "rejected", "notes": ["a quarter's figure, moved back to Q4"]})
            derived = self.values.get((v["group_key"], v["kpi_key"], v["fiscal_year"], "Q4"))
            if derived and derived["method"] == "derived":
                self.values.pop(value_key(derived), None)
        for start in range(0, len(back), 400):
            self._store(values=back[start:start + 400])
        if back:
            self.log(f"{self.symbol}: {len(back) // 2} quarterly figures moved back from FY to Q4")

    def _adopt_listed_keys(self, spec: Spec) -> None:
        """Stored rows of a table read whole move to the key of the listed KPI they are ("3-nanometer" → nm3), the key a
        row-by-row reading uses, so one quarter never holds a figure twice and years line up for Q4. No AI involved."""
        groups = {AI_GROUP_PREFIX + group.key: group for group in spec.groups}
        moved = []
        for v in list(self.values.values()):
            group = groups.get(v["group_key"])
            if group is None or v["method"] != "ai" or v["validation_status"] == "rejected":
                continue
            kpi = _listed_kpi(group, v["kpi_label"]) or _listed_kpi(group, v["kpi_key"])
            if kpi is None or kpi.key == v["kpi_key"]:
                continue
            target = self.values.get((v["group_key"], kpi.key, v["fiscal_year"], v["fiscal_period"]))
            if target and target["validation_status"] == "verified" and v["validation_status"] != "verified":
                moved.append({**v, "validation_status": "rejected", "notes": [f"same figure as {kpi.key}"]})
                continue
            moved.append({**v, "kpi_key": kpi.key, "kpi_label": kpi.label})
            moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {kpi.key}"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])

    def _join_custom_axes(self) -> None:
        """A company's own early axis continued by a standard one (Nvidia's 2016–18 "Revenue by Major Market" became its
        Revenue by Product): its periods join the standard group when most of its rows are rows of that group and every
        period the two share holds the same figures. No AI involved."""
        groups: dict[str, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "xbrl" and v["validation_status"] != "rejected":
                groups.setdefault(v["group_key"], []).append(v)
        standard = {key: rows for key, rows in groups.items() if not key.startswith(("x_", AI_GROUP_PREFIX))}
        moved = []
        for key, rows in groups.items():
            if not key.startswith("x_"):
                continue
            own = {v["kpi_key"] for v in rows}
            shared, target = max(((len(own & {v["kpi_key"] for v in other}), name) for name, other in standard.items()),
                                 default=(0, None))
            if target is None or shared < 2 or shared * 2 < len(own):
                continue
            theirs = {(v["fiscal_year"], v["fiscal_period"], v["kpi_key"]): float(v["value"]) for v in standard[target]}
            both = {(v["fiscal_year"], v["fiscal_period"]) for v in standard[target]}
            if any((v["fiscal_year"], v["fiscal_period"]) in both
                   and not _same(theirs.get((v["fiscal_year"], v["fiscal_period"], v["kpi_key"]), float("nan")), float(v["value"]))
                   for v in rows):
                continue  # a shared period read differently: two views after all
            latest = max(standard[target], key=lambda v: v["period_end"])
            for v in rows:
                if (v["fiscal_year"], v["fiscal_period"]) not in both:
                    moved.append({**v, "group_key": target, "group_label": latest["group_label"],
                                  "group_order": latest["group_order"]})
                moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {target}"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])

    def _unify_series(self) -> None:
        """One key per business across a company's whole XBRL history, so Q4 = year − quarters always lines up.
        Two kinds of evidence link keys: the same XBRL element (a label change: "Dynamics" became "Dynamics products and
        cloud services"), and a member whose restated prior-year figure equals, to the unit, what another key reported
        for that year (a new element: Microsoft's "Xbox" restates FY2025 "Gaming"). Microsoft moved Search between two
        elements and back, so neither alone suffices; together they link the whole history. A linked set that ever has
        two of its keys in one period is two businesses after all and is left alone. The series takes its latest key,
        so it carries the latest name."""
        rows = [v for v in self.values.values() if v["method"] == "xbrl" and v["validation_status"] != "rejected"]
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(node):
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a, b):
            parent[find(a)] = find(b)

        by_element: dict[tuple[str, str], tuple[str, str]] = {}
        index: dict[tuple[str, bool], list[dict]] = {}
        for v in rows:
            node = (v["group_key"], v["kpi_key"])
            find(node)
            element = (v.get("locator") or {}).get("member")
            if element:
                if (v["group_key"], element) in by_element:
                    union(node, by_element[(v["group_key"], element)])
                else:
                    by_element[(v["group_key"], element)] = node
            index.setdefault((v["group_key"], v["fiscal_period"] == "FY"), []).append(v)
        for v in rows:
            prior = (v.get("locator") or {}).get("prior")
            if not prior:
                continue
            target = (date.fromisoformat(v["period_end"][:10]) - timedelta(days=365)).isoformat()
            matches = {(o["group_key"], o["kpi_key"]) for o in index.get((v["group_key"], v["fiscal_period"] == "FY"), [])
                       if abs(_days(o["period_end"], target)) <= 12 and _same(float(o["value"]), float(prior))}
            if len(matches) == 1:
                union((v["group_key"], v["kpi_key"]), matches.pop())
        components: dict[tuple[str, str], list[dict]] = {}
        for v in rows:
            components.setdefault(find((v["group_key"], v["kpi_key"])), []).append(v)
        rekeyed = []
        for members in components.values():
            keys = {v["kpi_key"] for v in members}
            if len(keys) < 2:
                continue
            periods = [(v["fiscal_year"], v["fiscal_period"]) for v in members]
            if len(periods) != len(set(periods)):
                continue  # two of its keys in one period: different businesses after all
            canonical = max(members, key=lambda v: (v["period_end"], v["fiscal_period"] == "FY"))["kpi_key"]
            for v in members:
                if v["kpi_key"] != canonical:
                    rekeyed.append({**v, "kpi_key": canonical})
                    rekeyed.append({**v, "validation_status": "rejected", "notes": [f"continued as {canonical}"]})
        for start in range(0, len(rekeyed), 400):
            self._store(values=rekeyed[start:start + 400])
        if rekeyed:
            self.log(f"{self.symbol}: {len(rekeyed) // 2} XBRL values joined to their series")

    def _adopt_restated_quarters(self) -> None:
        """A year's quarter that is missing or lacks a row of the year's own 10-K (Alphabet's 2019 10-Qs grouped Search
        and YouTube as "Google properties" and left "Google other" untagged in Q1) is taken from the following year's
        10-Q for the same quarter, which restates it on the year's layout: only when that restated quarter has exactly
        the year's rows and adds up to the quarter's reported revenue. The year's Q4 then follows on one layout."""
        rows: dict[tuple[str, str, str], dict[str, dict]] = {}
        revenue: dict[tuple[str, str, str], tuple[float, str]] = {}
        for v in self.values.values():
            if v["method"] != "xbrl" or v["validation_status"] != "verified" or v["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            rows.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
            total = (v.get("locator") or {}).get("total")
            if isinstance(total, (int, float)):
                # Each group's own total: segment operating income does not add up to revenue.
                revenue.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), (float(total), v["period_end"]))
        changed = []
        # A year with quarters but no figure of its own (Nvidia's FY2018 10-K tags no regions for the year) takes the
        # year as the following 10-K restates it, when that restatement adds up to the revenue it restates.
        for (group, year, period), following in list(rows.items()):
            previous = str(int(year) - 1)
            if period != "FY" or (group, previous, "FY") in rows or not any((group, previous, q) in rows for q in ("Q1", "Q2", "Q3")):
                continue
            priors = {key: (row.get("locator") or {}).get("prior") for key, row in following.items()}
            restated_total = next((float(t) for row in following.values()
                                   if isinstance(t := (row.get("locator") or {}).get("prior_total"), (int, float))), None)
            end = next((f["period_end"] for f in self.filings.values() if f.get("document_role") == "periodic_report"
                        and f.get("fiscal_year") == previous and f.get("fiscal_period") == "FY" and f.get("period_end")), None)
            if (not all(isinstance(v, (int, float)) for v in priors.values()) or restated_total is None or end is None
                    or abs(sum(priors.values()) - restated_total) > abs(restated_total) * 0.001):
                continue
            year_rows = {key: {**row, "fiscal_year": previous, "period_end": end, "value": float(priors[key]),
                               "reconciliation_error_pct": (sum(priors.values()) - restated_total) / restated_total,
                               "locator": {"total": restated_total, "member": (row.get("locator") or {}).get("member"),
                                           "restated_by": row.get("source_accession")},
                               "notes": ["as restated in the following year's filing"]} for key, row in following.items()}
            rows[(group, previous, "FY")] = year_rows
            changed += year_rows.values()
        for (group, year, period), annual in list(rows.items()):
            if period != "FY":
                continue
            for quarter in ("Q1", "Q2", "Q3"):
                current = rows.get((group, year, quarter)) or {}
                if current and not {key for key in set(annual) - set(current) if not balancing(key)}:
                    continue  # on the year's layout, or finer (Nvidia's 10-Qs list regions its 10-K folds into Other)
                following = rows.get((group, str(int(year) + 1), quarter)) or {}
                # A business the following year added (Amazon's physical stores, from Whole Foods in August 2017) shows
                # as nothing in the restated quarter: not a row of the year's layout, so left out.
                following = {key: row for key, row in following.items()
                             if key in annual or (row.get("locator") or {}).get("prior") not in (0, 0.0, None)}
                priors = {key: (row.get("locator") or {}).get("prior") for key, row in following.items()}
                if not priors or set(priors) != set(annual) or not all(isinstance(v, (int, float)) for v in priors.values()):
                    continue
                # The restated rows must add up to revenue: the quarter's own when its report was read, else the
                # revenue the restating report gives for that quarter.
                restated_total = next((float(t) for row in following.values()
                                       if isinstance(t := (row.get("locator") or {}).get("prior_total"), (int, float))), None)
                reported = revenue.get((group, year, quarter))
                if reported is None and restated_total is not None:
                    # The quarter's own report was read but kept nothing: its record still knows when the quarter ended.
                    end = next((f["period_end"] for f in self.filings.values() if f.get("document_role") == "periodic_report"
                                and f.get("fiscal_year") == year and f.get("fiscal_period") == quarter and f.get("period_end")),
                               (date.fromisoformat(next(iter(following.values()))["period_end"][:10]) - timedelta(days=364)).isoformat())
                    reported = (restated_total, end)
                if reported is None or abs(sum(priors.values()) - reported[0]) > abs(reported[0]) * 0.001:
                    continue
                note = "as restated in the following year's filing"
                changed += [{**row, "fiscal_year": year, "fiscal_period": quarter, "period_end": reported[1],
                             "value": float(priors[key]), "reconciliation_error_pct": (sum(priors.values()) - reported[0]) / reported[0],
                             "locator": {"total": reported[0], "member": (row.get("locator") or {}).get("member"),
                                         "restated_by": row.get("source_accession")},
                             "notes": [note]} for key, row in following.items()]
                changed += [{**row, "validation_status": "rejected", "notes": ["replaced by the following year's restated layout"]}
                            for key, row in current.items() if key not in priors]
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])
        adopted = sum(1 for v in changed if v["validation_status"] == "verified")
        if adopted:
            self.log(f"{self.symbol}: {adopted} quarterly values taken from the following year's restated layout")

    def _unify_release_series(self, releases: list[FilingRef]) -> None:
        """A line a release renamed (Alphabet's "Google other" became "Google subscriptions, platforms, and devices" in
        Q4 2023) is one series under its new name: the release's year-ago cell in the new line's row equals what the old
        line reported a year earlier. Without it a year's quarters carry two names and the year cannot be added up. The
        release comes from the filing archive; no AI involved."""
        by_accession = {ref.accession: ref for ref in releases}
        groups: dict[str, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] == "verified" and v["group_kind"] != "metric":
                groups.setdefault(v["group_key"], []).append(v)
        moved = []
        for group, rows in groups.items():
            quarters = [v for v in rows if v["fiscal_period"] != "FY"]
            if not quarters:
                continue
            first = {key: min(v["period_end"] for v in quarters if v["kpi_key"] == key) for key in {v["kpi_key"] for v in quarters}}
            last = {key: max(v["period_end"] for v in quarters if v["kpi_key"] == key) for key in first}
            start, end = min(first.values()), max(last.values())
            ended = [key for key in first if last[key] < end]
            for key in [key for key in first if first[key] > start] if ended else []:
                opening = next(v for v in quarters if v["kpi_key"] == key and v["period_end"] == first[key])
                ref = by_accession.get(opening.get("source_accession") or "")
                if ref is None or (opening.get("locator") or {}).get("row") is None:
                    continue
                target = (date.fromisoformat(opening["period_end"][:10]) - timedelta(days=365)).isoformat()
                before = {old: float(v["value"]) for v in quarters for old in ended
                          if v["kpi_key"] == old and abs(_days(v["period_end"], target)) <= 12}
                try:
                    cells = _year_ago_cells(self._document(ref), opening["locator"], float(opening["value"]))
                except Exception:  # noqa: BLE001 - an unreadable release links nothing
                    continue
                matches = [old for old, value in before.items() if any(abs(cell - value) <= abs(value) * 1e-4 for cell in cells)]
                if len(matches) != 1 or any(v["kpi_key"] == key for v in rows for o in rows
                                            if o["kpi_key"] == matches[0] and o["fiscal_year"] == v["fiscal_year"]
                                            and o["fiscal_period"] == v["fiscal_period"]):
                    continue  # no single predecessor, or the two lines share a period: different businesses
                for v in rows:
                    if v["kpi_key"] == matches[0]:
                        moved.append({**v, "kpi_key": key, "kpi_label": opening["kpi_label"]})
                        moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {key}"]})
        for start_at in range(0, len(moved), 400):
            self._store(values=moved[start_at:start_at + 400])
        if moved:
            self.log(f"{self.symbol}: {len(moved) // 2} release values joined to their renamed series")

    def _anchor_ai_breakdowns(self) -> None:
        """An AI breakdown's total must be a figure the company reported in XBRL for the same period: its revenue, or a
        segment it splits (AMD's "Client and Gaming"). Checked every run, so a release read before its 10-Q arrived is
        checked once it has. A breakdown that matches nothing reported is flagged, all of it."""
        # (period end, figure, the member it is when it is one segment rather than revenue)
        reported: dict[str, list[tuple[str, float, str | None]]] = {}
        for v in self.values.values():
            if v["method"] in ("xbrl", "derived") and not v["group_key"].startswith(AI_GROUP_PREFIX) \
                    and v["validation_status"] == "verified":
                figures = [(float(v["value"]), v["kpi_label"])]
                if (v.get("locator") or {}).get("total"):
                    figures.append((float(v["locator"]["total"]), None))
                reported.setdefault(v["fiscal_period"], []).extend((v["period_end"], f, member) for f, member in figures)
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if (v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] == "verified"
                    and (v.get("locator") or {}).get("total")):
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), []).append(v)
        # Parts each proven on their own stand, whatever their read total row says.
        groups = {key: rows for key, rows in groups.items() if not all(_proven(v) for v in rows)}
        flagged = []
        segment_of: dict[str, set[str]] = {}
        for (group, _year, period), rows in groups.items():
            total, end = float(rows[0]["locator"]["total"]), rows[0]["period_end"]
            nearby = [(figure, member) for figure_end, figure, member in reported.get(period, [])
                      if abs(_days(figure_end, end)) <= 12]
            matches = [member for figure, member in nearby if abs(figure - total) <= abs(total) * 0.001]
            if nearby and not matches:
                flagged += [{**v, "validation_status": "needs_review",
                             "notes": [*(v.get("notes") or []), "total matches no figure the company reported in XBRL"]}
                            for v in rows]
            elif matches and all(member for member in matches):
                segment_of.setdefault(group, set()).add(matches[0])
        # A breakdown of one segment (AMD's Client + Gaming = its "Client and Gaming" segment) is titled for that
        # segment, so it never reads as company-wide revenue by product.
        for group, members in segment_of.items():
            if len(members) != 1:
                continue
            member = members.pop()
            for v in self.values.values():
                if v["group_key"] == group and v["validation_status"] != "rejected" and member not in v["group_label"]:
                    flagged.append({**v, "group_label": f"{member} {v['group_label']}"[:80]})
        for start in range(0, len(flagged), 400):
            self._store(values=flagged[start:start + 400])

    def _prove_flagged(self, releases: list[FilingRef]) -> None:
        """Settles flagged release values by evidence, with no AI and no one reviewing them:
        - a value equal to a figure the company reported in XBRL for the same period is proven;
        - a breakdown whose parts add up to such a figure is proven whole;
        - the next year's release repeats each figure in its year-ago column: the same number proves it, a single other
          number is the figure as restated and replaces it, and none matching among several rejects it.
        What nothing settles stays unserved. A later release is fetched for a value only until it has been checked."""
        flagged = [v for v in self.values.values() if v["method"] == "ai" and v["validation_status"] == "needs_review"]
        official: dict[bool, list[tuple[str, float]]] = {}
        for v in self.values.values():
            if (v["method"] in ("xbrl", "derived") and not v["group_key"].startswith(AI_GROUP_PREFIX)
                    and v["validation_status"] == "verified"):
                figures = official.setdefault(v["fiscal_period"] == "FY", [])
                figures.append((v["period_end"], float(v["value"])))
                if (v.get("locator") or {}).get("total"):
                    figures.append((v["period_end"], float(v["locator"]["total"])))

        def reported(row: dict, figure: float, tolerance: float) -> bool:
            return any(abs(_days(end, row["period_end"])) <= 12 and abs(other - figure) <= abs(figure) * tolerance
                       for end, other in official.get(row["fiscal_period"] == "FY", []))

        def settle(row: dict, note: str, **fields) -> None:
            settled[value_key(row)] = {**row, "validation_status": "verified", **fields,
                                       "notes": [*(row.get("notes") or []), note]}

        settled: dict[tuple, dict] = {}
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)

        def whole(row: dict) -> bool:
            """A breakdown part counts as proven only in a breakdown that adds up to its own total: every visible part
            can match XBRL while a part the release left out (UnitedHealth's eliminations) still makes it incomplete."""
            if row["group_kind"] != "revenue_breakdown":
                return True
            rows = groups.get((row["group_key"], row["fiscal_year"], row["fiscal_period"], row.get("source_accession")), [])
            total = float((row.get("locator") or {}).get("total") or 0)
            return not total or abs(sum(float(o["value"]) for o in rows) - total) <= abs(total) * 0.001

        for v in flagged:
            value = float(v["value"])
            if v["unit"] == "currency" and _significant(value) and reported(v, value, 1e-4) and whole(v):
                settle(v, _EQUALS_XBRL)
        # A restatement far from the figure it replaced was a column of another length (a year for a quarter): the
        # figure is restored and goes back to review.
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "verified":
                continue
            note = next((n for n in v.get("notes") or [] if n.startswith(_RESTATED_LATER)), None)
            match = re.search(r"\(was ([-\d.e+]+)\)", note or "")
            if match and abs(float(v["value"]) - float(match.group(1))) > abs(float(match.group(1))) * MAX_RESTATEMENT:
                settled[value_key(v)] = {**v, "value": float(match.group(1)), "validation_status": "needs_review",
                                         "notes": [n for n in v.get("notes") or [] if n != note]}
        # Parts proven alone by an earlier version of this rule, in breakdowns that do not add up, go back to review.
        for rows in groups.values():
            if not whole(rows[0]):
                for v in rows:
                    if v["validation_status"] == "verified" and {_EQUALS_XBRL, _ADDS_TO_XBRL} & set(v.get("notes") or []):
                        settled[value_key(v)] = {**v, "validation_status": "needs_review", "notes": [
                            note for note in v.get("notes") or [] if note not in (_EQUALS_XBRL, _ADDS_TO_XBRL)]}
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review" and value_key(v) not in settled]
            # To the figures' own rounding: Unity's release gives its Q3 2024 lines to the million ($447 million in all),
            # its XBRL revenue to the thousand ($446.5 million).
            parts = sum(float(v["value"]) for v in rows)
            rounding = sum(_rounding(float(v["value"])) for v in rows) / abs(parts) if parts else 0
            if waiting and len(rows) >= 2 and whole(rows[0]) and reported(rows[0], parts, max(1e-5, min(rounding, 0.005))):
                for v in waiting:
                    settle(v, _ADDS_TO_XBRL)
        # A split of part of a line (Adobe's customer groups of its subscription revenue, with their own total that XBRL
        # never tags) is proven by a reported figure it nearly fills, when its parts add up to its stated total to their
        # rounding and nothing else is wrong with it.
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review" and value_key(v) not in settled]
            if len(rows) < 2 or not waiting or any(not {n for n in v.get("notes") or [] if not _EXACT_NOTE.match(n)} <= _UNANCHORED
                                                   for v in waiting):
                continue
            parts = [float(v["value"]) for v in rows]
            stated = float((rows[0].get("locator") or {}).get("total") or 0) or sum(parts)
            if abs(sum(parts) - stated) > sum(_rounding(value) for value in [*parts, stated]):
                continue
            if any(abs(_days(end, rows[0]["period_end"])) <= 12 and _MIN_COVERAGE * other <= stated <= other
                   for end, other in official.get(rows[0]["fiscal_period"] == "FY", [])):
                for v in waiting:
                    settle(v, _WITHIN_XBRL)
        # A breakdown period takes the next year's restated figures whole or not at all: a part the next release no longer
        # shows (TSMC's 16/20nm, split in two from 2019) left beside restated parts made Q3 2018 add up to neither total.
        restatements: dict[tuple, list[tuple[dict, float, str]]] = {}

        def period_of(row: dict) -> tuple:
            return (row["group_key"], row["fiscal_year"], row["fiscal_period"])

        def ai_parts(row: dict) -> list[dict]:
            return [o for o in self.values.values() if period_of(o) == period_of(row)
                    and o["method"] == "ai" and o["validation_status"] != "rejected"]

        # Parts restated alone before this rule, beside parts that were not: the period goes back to its own figures.
        for v in self.values.values():
            if v["method"] != "ai" or v["group_kind"] != "revenue_breakdown" or v["validation_status"] == "rejected":
                continue
            note = next((n for n in v.get("notes") or [] if n.startswith(_RESTATED_LATER)), None)
            match = re.search(r"\(was ([-\d.e+]+)\)", note or "")
            if match and not all(any(n.startswith(_RESTATED_LATER) for n in o.get("notes") or []) for o in ai_parts(v)):
                settled[value_key(v)] = {**v, "value": float(match.group(1)), "validation_status": "needs_review",
                                         "notes": [n for n in v.get("notes") or [] if n != note]}
        by_accession = {ref.accession: ref for ref in releases}
        for v in flagged:
            if value_key(v) in settled:
                continue
            target = (date.fromisoformat(v["period_end"][:10]) + timedelta(days=365)).isoformat()
            later = next((o for o in self.values.values()
                          if o["method"] == "ai" and o["validation_status"] == "verified"
                          and o["group_key"] == v["group_key"] and o["kpi_key"] == v["kpi_key"]
                          and (o["fiscal_period"] == "FY") == (v["fiscal_period"] == "FY")
                          and abs(_days(o["period_end"], target)) <= 12 and o.get("source_accession") in by_accession
                          and (o.get("locator") or {}).get("row") is not None), None)
            if later is None:
                continue
            checked = f"checked against {later['source_accession']}"
            if checked in (v.get("notes") or []):
                continue
            try:
                document = self._document(by_accession[later["source_accession"]])
            except Exception:  # noqa: BLE001 - an unreadable release settles nothing
                continue
            cells = _year_ago_cells(document, later["locator"], float(later["value"]))
            value = float(v["value"])
            named = _period_headed(document, later["locator"])
            if any(abs(cell - value) <= max(abs(value) * 1e-4, 1e-9) for cell in cells):
                settle(v, _CONFIRMED_LATER)
            elif named and len(cells) == 1 and abs(cells[0] - value) <= abs(value) * MAX_RESTATEMENT:
                if v["group_kind"] == "revenue_breakdown":
                    restatements.setdefault(period_of(v), []).append((v, round(cells[0], 2), checked))
                else:
                    settle(v, f"{_RESTATED_LATER} (was {value:.15g})", value=round(cells[0], 2))
            elif named and len(cells) > 1:
                settled[value_key(v)] = {**v, "validation_status": "rejected",
                                         "notes": [*(v.get("notes") or []), "contradicted by the next year's release"]}
            else:
                settled[value_key(v)] = {**v, "notes": [*(v.get("notes") or []), checked]}
        for period, items in restatements.items():
            restated = {value_key(row) for row, _figure, _checked in items}
            parts = ai_parts(items[0][0])
            if all(value_key(o) in restated or _CONFIRMED_LATER in ((settled.get(value_key(o)) or o).get("notes") or [])
                   for o in parts):
                for row, figure, _checked in items:
                    settle(row, f"{_RESTATED_LATER} (was {float(row['value']):.15g})", value=figure)
            else:
                # Kept as filed; recorded as checked so the next release is not read for it again.
                for row, _figure, checked in items:
                    settled[value_key(row)] = {**row, "notes": [*(row.get("notes") or []), checked]}
        # A breakdown or mix is proven whole or not at all: a part confirmed on its own, beside parts still flagged, stays
        # flagged with them (its confirmation note kept for when the rest is settled).
        for key, row in list(settled.items()):
            if row["validation_status"] != "verified" or row["group_kind"] == "metric":
                continue
            siblings = [v for v in self.values.values() if v["group_key"] == row["group_key"]
                        and v["fiscal_year"] == row["fiscal_year"] and v["fiscal_period"] == row["fiscal_period"]
                        and v["method"] == "ai" and v["validation_status"] != "rejected"]
            if any((settled.get(value_key(v)) or v)["validation_status"] != "verified" for v in siblings):
                settled[key] = {**row, "validation_status": "needs_review"}
        changed = list(settled.values())
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])
        proven = sum(1 for v in changed if v["validation_status"] == "verified")
        if proven:
            self.log(f"{self.symbol}: {proven} flagged values proven by later or official figures")
        self._release_held_breakdowns()

    def _fix_mislabelled_quarters(self) -> None:
        """A filer's XBRL can attach figures to the wrong members (AMD's 2023 and 2024 10-Qs tag Data Center's $2,337M as
        Client). A later filing's comparatives then contradict them: the same member restated with a value another member
        of that quarter holds. The quarters of that year are relabelled with the one mapping that every later comparative
        and the year's own 10-K allow (no member's Q4 revenue below zero); if the evidence allows none or several, the
        mapping found for another group of the same filings (operating income takes revenue's) is used when it fits, else
        they are flagged, so swapped names are never served. Years go newest first: a relabelled year's own comparatives
        are the evidence for the year before (AMD's 2024 10-Qs restate 2023 with the same wrong tags)."""
        held = "member labels contradict later filings"
        done: set[tuple[str, str]] = set()
        for _round in range(6):
            rows = [v for v in self.values.values() if v["method"] == "xbrl" and (
                v["validation_status"] == "verified" or held in (v.get("notes") or []))]
            by_period: dict[tuple[str, str, str], dict[str, dict]] = {}
            for v in rows:
                by_period.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
            # Later comparatives: (group, quarter end) → {key: restated value}.
            restated: dict[tuple[str, str], dict[str, float]] = {}
            for v in rows:
                prior = (v.get("locator") or {}).get("prior")
                if prior is not None and v["fiscal_period"] != "FY" and v["validation_status"] == "verified":
                    end = (date.fromisoformat(v["period_end"][:10]) - timedelta(days=365)).isoformat()
                    restated.setdefault((v["group_key"], end), {})[v["kpi_key"]] = float(prior)
            contradicted = {item: quarters for item, quarters in self._contradicted_years(by_period, restated).items()
                            if item not in done}
            flagged = {(v["group_key"], v["fiscal_year"]) for v in rows if held in (v.get("notes") or [])
                       and v["fiscal_period"] != "FY" and (v["group_key"], v["fiscal_year"]) not in done}
            for group, year in flagged - set(contradicted):
                quarters = [q for q in (by_period.get((group, year, p)) for p in ("Q1", "Q2", "Q3")) if q]
                if quarters and len({frozenset(q) for q in quarters}) == 1:
                    contradicted[(group, year)] = quarters
            if not contradicted:
                break
            # Newest year first; revenue before operating income, whose mapping may be borrowed.
            (group, year), quarters = max(contradicted.items(), key=lambda item: (item[0][1], item[0][0] != INCOME_GROUP))
            done.add((group, year))
            changes = []
            keys = sorted(quarters[0])
            annual = by_period.get((group, year, "FY"), {})
            evidence = [(quarter, {k: x for k, x in _restated_for(restated, group, quarter[keys[0]]["period_end"]).items()
                                   if k in keys}) for quarter in quarters]

            def fits_evidence(mapping: dict[str, str]) -> bool:
                relabelled = [{mapping[k]: float(q[k]["value"]) for k in keys} for q in quarters]
                if any(any(not _same(relabelled[i][k], x) for k, x in found.items()) for i, (_q, found) in enumerate(evidence)):
                    return False
                # A segment's operating income may be a loss in Q4; its revenue may not.
                return group == INCOME_GROUP or set(annual) != set(keys) or not any(
                    float(annual[k]["value"]) - sum(r[k] for r in relabelled) < 0 for k in keys if not balancing(k))

            fits = [mapping for order in permutations(keys) if fits_evidence(mapping := dict(zip(keys, order)))]
            if len(fits) != 1:
                # Another group of the same filings, already on its true names, says which segment each member is.
                named: dict[tuple[str, str], str] = {}
                for v in self.values.values():
                    member = (v.get("locator") or {}).get("member")
                    if v["group_key"] != group and member and v["method"] == "xbrl" and v["validation_status"] == "verified":
                        named[(v.get("source_accession") or "", member)] = v["kpi_key"]
                borrowed = set()
                for quarter in quarters:
                    mapping = {}
                    for key, row in quarter.items():
                        true = named.get((row.get("source_accession") or "", (row.get("locator") or {}).get("member") or ""))
                        mapping[key] = true if true in keys else key
                    borrowed.add(tuple(sorted(mapping.items())))
                if len(borrowed) == 1 and sorted((mapping := dict(next(iter(borrowed)))).values()) == keys and fits_evidence(mapping):
                    fits = [mapping]
            if len(fits) == 1 and any(k != t for k, t in fits[0].items()):
                for quarter in quarters:
                    for key, row in quarter.items():
                        true = fits[0][key]
                        label = next((q[true]["kpi_label"] for q in quarters if true in q), true)
                        notes = [note for note in (row.get("notes") or []) if note != held]
                        changes.append({**row, "kpi_key": true, "kpi_label": label, "validation_status": "verified",
                                        "notes": [*notes, *([] if true == key else ["member labels corrected from later filings"])]})
            elif len(fits) == 1:
                changes += [{**row, "validation_status": "verified", "notes": [n for n in row.get("notes") or [] if n != held]}
                            for quarter in quarters for row in quarter.values() if held in (row.get("notes") or [])]
            elif not any(all(_same(float(q[k]["value"]), x) for k, x in found.items()) for q, found in evidence):
                changes += [{**row, "validation_status": "needs_review",
                             "notes": [*(row.get("notes") or []), *([] if held in (row.get("notes") or []) else [held])]}
                            for quarter in quarters for row in quarter.values()]
            for start in range(0, len(changes), 400):
                self._store(values=changes[start:start + 400])
            if changes:
                self.log(f"{self.symbol}: {len(changes)} XBRL values of {group} {year} relabelled or held from later filings' comparatives")

    @staticmethod
    def _contradicted_years(by_period: dict, restated: dict) -> dict[tuple[str, str], list[dict]]:
        """(group, fiscal year) → its Q1–Q3 rows, for years where a later comparative gives a member a value another
        member of that quarter holds."""
        years: dict[tuple[str, str], list[dict]] = {}
        for (group, year, period), quarter in by_period.items():
            if period == "FY":
                continue
            found = _restated_for(restated, group, next(iter(quarter.values()))["period_end"])
            values = {key: float(row["value"]) for key, row in quarter.items()}
            if any(key in values and not _same(values[key], x) and any(_same(x, other) for k2, other in values.items() if k2 != key)
                   for key, x in found.items()):
                years[(group, year)] = []
        for (group, year) in list(years):
            quarters = [by_period.get((group, year, q)) for q in ("Q1", "Q2", "Q3")]
            quarters = [q for q in quarters if q]
            if not quarters or len({frozenset(q) for q in quarters}) != 1 or len(quarters[0]) > 6:
                del years[(group, year)]
                continue
            years[(group, year)] = quarters
        return years

    def _q4_gaps(self) -> list[str]:
        periods: dict[tuple[str, str], set[str]] = {}
        for v in self.values.values():
            if v["group_kind"] == "revenue_breakdown" and v["validation_status"] == "verified":
                periods.setdefault((v["group_label"], v["fiscal_year"]), set()).add(v["fiscal_period"])
        return sorted(f"{label} {year}" for (label, year), found in periods.items()
                      if {"FY", "Q1", "Q2", "Q3"} <= found and "Q4" not in found)

    def _recheck_flagged(self) -> None:
        """Clears flags that stored figures themselves disprove, without reading anything again:
        - a full year flagged for jumping against a quarter is compared with the year before instead;
        - a table read whole without its total row reconciles to a figure the same filing reports for the same period
          (TSMC's geography to its net revenue, its nodes to wafer revenue), to within 0.001%."""
        cleared = []
        by_filing: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] != "rejected":
                by_filing.setdefault((v.get("source_accession") or "", v["period_end"]), []).append(v)
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "needs_review" or v["fiscal_period"] != "FY":
                continue
            notes = v.get("notes") or []
            if notes and all(_is_jump(note) and note.endswith("in a quarter") for note in notes):
                key = f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}"
                before = self._previous(date.fromisoformat(v["period_end"][:10]), annual=True).get(key)
                siblings = by_filing.get((v.get("source_accession") or "", v["period_end"]), [])
                parts = sum(float(o["value"]) for o in siblings if o["group_key"] == v["group_key"])
                total = float((v.get("locator") or {}).get("total") or 0)
                if total and abs(parts - total) <= abs(total) * 0.001:
                    # A new node ramping 3.5× in a year is growth; the year's table adding up exactly is the check.
                    cleared.append({**v, "validation_status": "verified", "notes": ["the year's breakdown reconciles"]})
                elif before is None or 1 / MAX_YEAR_RATIO <= float(v["value"]) / before <= MAX_YEAR_RATIO:
                    cleared.append({**v, "validation_status": "verified", "notes": ["checked against the prior year"]})
        for (accession, _end), rows in by_filing.items():
            for group_key in {v["group_key"] for v in rows if v["group_kind"] == "revenue_breakdown"}:
                members = [m for m in rows if m["group_key"] == group_key]
                total = sum(float(m["value"]) for m in members)
                for m in members:
                    notes = m.get("notes") or []
                    if (m["validation_status"] == "needs_review" and notes and all(_is_jump(note) for note in notes)
                            and total and abs(float(m["value"])) < 0.01 * abs(total)):
                        cleared.append({**m, "validation_status": "verified", "notes": ["immaterial row; the total checks it"]})
            groups: dict[str, list[dict]] = {}
            for v in rows:
                groups.setdefault(v["group_key"], []).append(v)
            for group_key, members in groups.items():
                if not all(m["validation_status"] == "needs_review" and (m.get("locator") or {}).get("whole_table")
                           and (m.get("notes") or []) == ["no total revenue to reconcile against"] for m in members):
                    continue
                parts = sum(float(m["value"]) for m in members)
                anchors = [float(o["value"]) for o in rows if o["group_key"] != group_key and o["validation_status"] == "verified"]
                anchors += [float((o.get("locator") or {}).get("total")) for o in rows
                            if o["group_key"] != group_key and o["validation_status"] == "verified" and (o.get("locator") or {}).get("total")]
                if len(members) >= 2 and any(abs(parts - anchor) <= abs(anchor) * 1e-5 for anchor in anchors):
                    cleared += [{**m, "validation_status": "verified", "locator": {**m["locator"], "total": parts},
                                 "reconciliation_error_pct": 0, "notes": ["reconciled to another figure in the same report"]}
                                for m in members]
        cleared += self._scale_runs()
        cleared += self._outdated_flags()
        for start in range(0, len(cleared), 400):
            self._store(values=cleared[start:start + 400])
        self._release_held_breakdowns()

    def _scale_runs(self) -> list[dict]:
        """A run of a metric's figures read without their table's scale (Visa's "Processed Transactions (millions)",
        29,321 for 29.3 billion from Q2 2018 to Q3 2020): the series steps by almost exactly a thousand, a million or a
        billion where the run starts and ends, so it splits into stretches by those steps, and the stretches off the
        scale most of its figures share are put back on it. Only the run's edges jump, so a jump check alone flags two
        quarters and leaves the run between them; an owner's figure is never changed, and percentages have no scale."""
        series: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if (v["method"] == "ai" and v["group_kind"] == "metric" and v["validation_status"] != "rejected"
                    and v["unit"] != "percent" and float(v["value"]) > 0):
                series.setdefault((v["group_key"], v["kpi_key"], v["fiscal_period"] == "FY"), []).append(v)
        records = []
        for rows in series.values():
            rows.sort(key=lambda v: (v["period_end"], v["fiscal_period"]))
            levels, level = [], 0
            for previous, current in zip([None, *rows], rows):
                if previous is not None:
                    step = math.log10(float(current["value"]) / float(previous["value"]))
                    thousands = round(step / 3)
                    if thousands and abs(step - 3 * thousands) < 0.3:
                        level += thousands
                levels.append(level)
            counts = {item: levels.count(item) for item in set(levels)}
            if len(counts) < 2:
                continue
            main = max(counts, key=lambda item: (counts[item], -abs(item)))
            if counts[main] * 2 <= len(rows):
                continue  # no scale most figures share
            for index, (v, at) in enumerate(zip(rows, levels)):
                if at == main and v["validation_status"] == "needs_review" and index and levels[index - 1] != main \
                        and all(_is_jump(note) or note == "confirmed by review" for note in v.get("notes") or []):
                    # Its jump was from the run before it.
                    records.append({**v, "validation_status": "verified", "notes": ["the figure before it was off its scale"]})
                if at == main or v.get("owner_decision"):
                    continue
                factor = 10 ** (3 * (main - at))
                notes = [note for note in v.get("notes") or [] if not _is_jump(note) and note != "confirmed by review"]
                records.append({**v, "value": round(float(v["value"]) * factor, 6),
                                "validation_status": "verified" if not notes else v["validation_status"],
                                "notes": [*notes, f"read without its table's scale: {factor:g}× to match the series"]})
        if records:
            self.log(f"{self.symbol}: {len(records)} figures put back on their series' scale")
        return records

    def _outdated_flags(self) -> list[dict]:
        """Flags set by a check that today's checks no longer raise, cleared from the stored figures alone:
        - a small count's ratio jump (ASML's used systems sold, 10 to 3), flagged before small counts moved by units;
        - a breakdown's parts off their total (ASML's 2023 quarters, read twice over while two keys named each line),
          now that its parts add up to the release's stated total."""
        aside = ("confirmed by review", "checked against ")
        cleared = []
        series: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] == "verified" and v["fiscal_period"] != "FY":
                series.setdefault((v["group_key"], v["kpi_key"]), []).append(v)
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "needs_review" or v["fiscal_period"] == "FY":
                continue
            reasons = [note for note in v.get("notes") or [] if not note.startswith(aside)]
            if v["unit"] != "count" or not reasons or not all(note.startswith("changed ") for note in reasons):
                continue
            before = max((o for o in series.get((v["group_key"], v["kpi_key"]), [])
                          if 0 < _days(v["period_end"], o["period_end"]) <= 120), key=lambda o: o["period_end"], default=None)
            if before is not None and count_jump(v["unit"], float(before["value"]), float(v["value"])) == "":
                cleared.append({**v, "validation_status": "verified",
                                "notes": [*(v.get("notes") or []), "a small count's move, within the rule for small counts"]})
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review"]
            stated = float((rows[0].get("locator") or {}).get("total") or 0)
            if not waiting or len(rows) < 2 or not stated or len(waiting) != len(rows):
                continue
            if not all([note for note in v.get("notes") or [] if not note.startswith(aside)]
                       and all(note.startswith("parts differ from the total by") for note in v.get("notes") or []
                               if not note.startswith(aside)) for v in waiting):
                continue
            parts = [float(v["value"]) for v in rows]
            if abs(sum(parts) - stated) <= abs(stated) * 0.001:
                cleared += [{**v, "validation_status": "verified", "reconciliation_error_pct": (sum(parts) - stated) / stated,
                             "notes": [*(v.get("notes") or []), "the parts add up to the release's total"]} for v in rows]
        return cleared

    def _release_held_breakdowns(self) -> None:
        """Parts flagged only because another part of their breakdown was, released once that part is cleared: TSMC's
        FY2024 nodes were held for 3nm's 3.5× ramp, which the year's reconciling table then confirmed."""
        held = "another part of this breakdown needs review"
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] != "metric" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)
        released = []
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review"]
            if waiting and all(set(v.get("notes") or []) <= {held, "confirmed by review"} and held in (v.get("notes") or [])
                               for v in waiting):
                released += [{**v, "validation_status": "verified",
                              "notes": [note for note in (v.get("notes") or []) if note != held]} for v in waiting]
        for start in range(0, len(released), 400):
            self._store(values=released[start:start + 400])

    def _reports_full_year(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown in this document adds up to the fiscal year's revenue in XBRL (an annual report the
        AI took for a quarter), whatever the AI said."""
        annual_totals = [
            float(value["locator"]["total"]) for value in self.values.values()
            if value["method"] == "xbrl" and value["fiscal_period"] == "FY" and (value.get("locator") or {}).get("total")
            and abs(_days(value["period_end"], period_end.isoformat())) <= 12
        ]
        if not annual_totals:
            return False
        sums: dict[str, float] = {}
        for item in read:
            if item.group.kind != "revenue_breakdown":
                continue
            if item.kpi.key == item.group.total_kpi:
                sums[item.group.key] = item.value
            elif item.group.total_kpi is None:
                sums[item.group.key] = sums.get(item.group.key, 0.0) + item.value
        return any(abs(total - annual) <= abs(annual) * 0.01 for total in sums.values() for annual in annual_totals)

    def _earlier_values(self, before: date) -> dict[str, list[float]]:
        earlier: dict[str, list[float]] = {}
        chained = getattr(self._local, "chained", False)
        for value in list(self.values.values()):
            if chained and not _same_chain(value["period_end"], before):
                continue  # the other chains' quarters are compared after reading, in date order
            if value["method"] == "ai" and value["period_end"] < (before - timedelta(days=20)).isoformat():
                key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
                earlier.setdefault(key, []).append(float(value["value"]))
        return earlier

    def _previous(self, before: date, annual: bool = False) -> dict[str, float]:
        """The latest verified value of each AI KPI in the period before, for jump checks: the quarter before a
        quarter, the year before a year (a year is never compared with a quarter)."""
        if getattr(self._local, "chained", False) and not annual:
            return {}  # quarters read in chains are compared with the quarter before after reading (_check_read_series)
        window_start = (before - timedelta(days=400 if annual else 120)).isoformat()
        latest: dict[str, dict] = {}
        for value in self.values.values():
            if (value["method"] != "ai" or value["validation_status"] != "verified"
                    or (value["fiscal_period"] == "FY") != annual
                    or not window_start <= value["period_end"] < before.isoformat()):
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] > latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        return {key: float(value["value"]) for key, value in latest.items()}

    def _default_currency(self) -> str | None:
        currencies = [value.get("currency") for value in self.values.values() if value["method"] == "xbrl"]
        return next((currency for currency in reversed(currencies) if currency), None)

    # Derived periods and storage.

    def _derive(self, spec: Spec | None = None) -> None:
        changed = []
        # A template KPI that is a line of another (Apollo's Credit and Equity management fees, lines of its management
        # fees) carries that parent, so a worked-out period is checked on its top level only.
        parents = {(f"{AI_GROUP_PREFIX}{group.key}", kpi.key): kpi.parent for group in (spec.groups if spec else [])
                   for kpi in group.kpis if kpi.parent}
        values = [{**v, "locator": {**(v.get("locator") or {}), "parent": parents[(v["group_key"], v["kpi_key"])]}}
                  if (v["group_key"], v["kpi_key"]) in parents else v for v in self.values.values()]
        derived = [{**record, "locator": {k: v for k, v in (record.get("locator") or {}).items() if k != "parent"} or None}
                   if (record["group_key"], record["kpi_key"]) in parents else record for record in derive_periods(values)]
        produced = {value_key(record) for record in derived}
        # Derived values whose inputs are gone or were replaced (e.g. a Q4 from a rejected reading) are withdrawn.
        changed.extend({**value, "validation_status": "rejected", "notes": ["inputs no longer available"]}
                       for key, value in self.values.items()
                       if value["method"] == "derived" and value["validation_status"] != "rejected" and key not in produced)
        for record in derived:
            existing = self.values.get(value_key(record))
            if existing and existing["method"] != "derived" and existing["validation_status"] == "verified":
                continue  # a reported figure wins; a flagged partial reading (TSMC's release percentages) does not
            if (existing and float(existing["value"]) == float(record["value"])
                    and existing["validation_status"] == record["validation_status"]):
                continue
            changed.append(record)
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])

    def _record(self, *, ref: FilingRef, period_end: date, source_url: str | None = None, **fields) -> dict:
        return {
            "symbol": self.symbol, **fields, "period_end": period_end.isoformat(),
            "source_accession": ref.accession, "source_url": source_url or ref.source_url,
            "filed_at": ref.filed.isoformat(),
        }

    def _store_filing(self, ref: FilingRef, status: str, *, records: list[dict] | None = None,
                      period_end: date | None = None, fiscal_year: str | None = None, fiscal_period: str | None = None,
                      spec_version: int | None = None, notes: list[str] | None = None, counted: bool = True) -> None:
        records = records or []
        previous = self.filings.get(ref.accession)
        # A re-read replaces the filing's earlier reading: values it no longer produces (a KPI key the new list renamed,
        # a figure now read differently, rows an older extractor kept) stop being served.
        # Only a reading that finished replaces the last one: a failed or empty attempt keeps the good values it had.
        method = "ai" if ref.role == "earnings_release" else "xbrl"
        if status in ("processed", "needs_review"):
            fresh = {value_key(record) for record in records}
            records = records + [
                {**value, "validation_status": "rejected", "notes": ["replaced by a newer reading of this filing"]}
                for value in self.values.values()
                if value["method"] == method and value.get("source_accession") == ref.accession
                and value["validation_status"] != "rejected" and value_key(value) not in fresh
                # A group not read this run keeps its rows (flagged, and so not served): they are why it is not read.
                and value["group_key"].removeprefix(AI_GROUP_PREFIX) not in (self._dead or set())
            ]
        filing = {
            "accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(), "document_role": ref.role,
            "period_end": period_end.isoformat() if period_end else None, "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period, "status": status, "spec_version": spec_version,
            "value_count": sum(1 for record in records if record["validation_status"] != "rejected"), "attempts": min(_STORED_ATTEMPTS, previous.get("attempts", 0) + 1) if previous else 1,
            "notes": [note[:300] for note in (notes or [])][:30], "source_url": ref.source_url,
        }
        self._store(filings=[filing], values=records)
        self.filings[ref.accession] = filing
        if not counted:
            return
        self.result.filings += 1
        self.result.needs_review += sum(1 for record in records if record["validation_status"] == "needs_review")
        self.log(f"{self.symbol}: {ref.form} {ref.filed} {status} ({filing['value_count']} values)")

    def _fail_filing(self, ref: FilingRef, error: Exception, spec_version: int | None = None) -> None:
        message = f"{type(error).__name__}: {error}"[:300]
        self.result.errors.append(f"{ref.accession}: {message}")
        self._store_filing(ref, "failed", spec_version=spec_version, notes=[message])

    def _store(self, *, filings: list[dict] | None = None, values: list[dict] | None = None) -> None:
        values = list({value_key(value): value for value in values or []}.values())  # one row per key per write
        # A note once: a check that settles a part each run, which the whole breakdown then holds back, added its note
        # again every run until JPM's reached the control plane's 40 and its run failed.
        values = [{**value, "notes": list(dict.fromkeys(value["notes"]))} if value.get("notes") else value for value in values]
        self.control.call("store", symbol=self.symbol, filings=filings or [], values=values)
        for value in values:
            existing = self.values.get(value_key(value))
            if existing and _keeps_verified(existing, value):
                continue  # as store_business_kpis does: another filing's unchecked reading never replaces a verified one
            self.values[value_key(value)] = value
        self.result.values += len(values)


def keep_current_groups(proposed: Spec, current: Spec) -> Spec:
    """A re-proposed list never silently drops a group the current list tracks (TSMC's platform and geography once
    vanished on a re-read); only the quarterly audit retires what a company stopped reporting."""
    keys = {group.key for group in proposed.groups}
    labels = {(group.kind, group.label.lower()) for group in proposed.groups}
    kept = [group for group in current.groups
            if group.key not in keys and (group.kind, group.label.lower()) not in labels]
    if not kept:
        return proposed
    groups = [*(group.model_dump() for group in proposed.groups), *(group.model_dump() for group in kept)]
    return Spec.model_validate({"groups": groups[:ai.MAX_GROUPS], "names": proposed.names})


def missing_pies(pies: list) -> bool:
    """Whether a slide's reading lacks a pie: every quarter it shows has a technology, end-use and region pie."""
    if not pies:
        return False
    quarters = {(pie.fiscal_year, pie.fiscal_period) for pie in pies}
    axes = {pie.axis for pie in pies} | {"technology", "end-use", "region"}
    return len(pies) < len(quarters) * len(axes)


def fill_pies(pies: list, closer: list) -> list:
    """The pies a closer reading has that the first did not."""
    have = {(pie.axis, pie.fiscal_year, pie.fiscal_period) for pie in pies}
    return [pie for pie in closer if (pie.axis, pie.fiscal_year, pie.fiscal_period) not in have]


def only_adds(before: Spec, after: Spec) -> bool:
    """Whether a new list keeps every KPI of the old one as it was, adding only."""
    kept = {(group.key, group.kind, kpi.key, kpi.unit) for group in after.groups for kpi in group.kpis}
    return all((group.key, group.kind, kpi.key, kpi.unit) in kept for group in before.groups for kpi in group.kpis)


_REVENUE_SUFFIX = re.compile(r"(?<=.)(netrevenues?|revenues?|netsales|sales)$")


def repeats_xbrl(group, covered: list[set[str]]) -> bool:
    """Whether a group (of any kind) names the same rows as a breakdown the company still tags in XBRL (Meta's Family
    of Apps and Reality Labs, once added as "metrics"): read by code with its whole history, never again from
    releases."""
    # A subtotal row the release adds ("Total Optum" beside its three segments, UnitedHealth) does not make it new.
    # "Agents and Infra Revenue" is the 10-Q's "Agents and Infra" (Microsoft's release names each line so).
    names = {_REVENUE_SUFFIX.sub("", _identity_text(kpi.label)) for kpi in group.kpis
             if kpi.key != group.total_kpi and not _identity_text(kpi.label).startswith("total")}
    tagged = [{_REVENUE_SUFFIX.sub("", member) for member in members} for members in covered]
    # Lines of one breakdown beside its segment's total from another (SpaceX's "SI revenues" beside its two lines).
    return len(names) >= 2 and (any(names <= members for members in tagged)
                                or (names <= set().union(*tagged) and any(len(names & members) >= 2 for members in tagged)))


def without_xbrl_repeats(spec: Spec, covered: list[set[str]], keep: Spec | None = None) -> Spec:
    """A proposed list without groups that repeat a breakdown still tagged in XBRL; a group the current list already
    has stays (the owner or the audit's history decides it)."""
    kept_keys = {group.key for group in keep.groups} if keep else set()
    groups = [group for group in spec.groups if group.key in kept_keys or not repeats_xbrl(group, covered)]
    if len(groups) == len(spec.groups):
        return spec
    return Spec.model_validate({"groups": [group.model_dump() for group in groups], "names": spec.names})


def apply_maintenance(spec: Spec, answer: ai.Maintenance, covered: list[set[str]] = ()) -> Spec | None:
    """The list with the audit's additions and retirements, or None when nothing changes. Existing keys keep their
    meaning; a new key that collides with an existing one is ignored; a group that repeats a breakdown the company
    still tags in XBRL is not added."""
    groups = [group.model_copy(deep=True) for group in spec.groups]
    by_key = {group.key: group for group in groups}
    changed = False
    for group_key, kpis in answer.add_kpis.items():
        group = by_key.get(group_key)
        if group is None:
            continue
        existing = {kpi.key for kpi in group.kpis}
        for kpi in kpis:
            unit_ok = group.kind == "metric" or kpi.unit == ("percent" if group.kind == "mix" else "currency")
            if kpi.key not in existing and unit_ok and len(group.kpis) < ai.MAX_KPIS_PER_GROUP:
                group.kpis.append(kpi)
                existing.add(kpi.key)
                changed = True
    amounts = {_dimension(group.label) for group in groups if group.kind == "revenue_breakdown"}
    for group in answer.add_groups:
        if group.kind == "mix" and _dimension(group.label) in amounts:
            continue  # shares of a breakdown already tracked as amounts add nothing (TSMC's technology mix)
        if repeats_xbrl(group, list(covered)):
            continue
        if group.key not in by_key and len(groups) < ai.MAX_GROUPS:
            groups.append(group)
            by_key[group.key] = group
            changed = True
    for retired in answer.retire:
        group_key, _, kpi_key = retired.partition(".")
        group = by_key.get(group_key)
        if group and kpi_key and len(group.kpis) > 1 and any(kpi.key == kpi_key for kpi in group.kpis):
            group.kpis = [kpi for kpi in group.kpis if kpi.key != kpi_key]
            if group.total_kpi == kpi_key:
                group.total_kpi = None
            changed = True
    if not changed:
        return None
    return ai.normalize_spec(Spec.model_validate({"groups": [group.model_dump() for group in groups], "names": spec.names}))


_ANNUAL_HEADER = re.compile(r"twelve months|<12 months>|<5[23] weeks>|fifty-(two|three) weeks|years? ended|fiscal years?|full year")
_QUARTER_HEADER = re.compile(r"three months|<3 months>|thirteen weeks|<1[34] weeks>|quarter|\bq[1-4]\b|\b[1-4]q\b")


def _quarter_column(value: dict) -> bool:
    """Whether a value was read from a column headed for three months, which no yearly total contradicts."""
    locator = value.get("locator") or {}
    header = str(locator.get("header") or "")
    return bool(_QUARTER_HEADER.search(header)) and not _ANNUAL_HEADER.search(header)


def _annual_header(header: str) -> bool:
    return bool(_ANNUAL_HEADER.search(header)) and not _QUARTER_HEADER.search(header)


def _annual_columns(read: list[ReadValue]) -> bool:
    """Whether the breakdown was read from a column its header calls a year ("Twelve Months Ended", "Year Ended")."""
    headers = [str(item.locator.get("header") or "") for item in read if item.group.kind == "revenue_breakdown"]
    return bool(headers) and all(_annual_header(header) for header in headers)


def _restated_for(restated: dict, group: str, end: str) -> dict[str, float]:
    """The later comparatives for a quarter, whose restated end can differ by days (52/53-week years)."""
    return next((values for (key, other), values in restated.items() if key == group and abs(_days(other, end)) <= 12), {})


def _year_ago_cells(document: Document, locator: dict, value: float) -> list[float]:
    """The other cells of a value's row in columns headed for the same length of period ("three months ended"), in the
    value's own units: the year-ago figure (and the quarter before, where a release shows it)."""
    table = document.tables.get(str(locator.get("table")))
    if table is None or locator.get("row") is None or locator.get("col") is None:
        return []
    row, col = int(locator["row"]), int(locator["col"])
    current = parse_number(table.cell(row, col))
    if current is None or not current.value:
        return []
    factor = value / current.value
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0]) and not all(re.fullmatch(r"(19|20)\d{2}", c.strip()) for c in r[1:] if c)), 1)
    period = column_header(table, first, col)  # "three months ended @ # #": years blanked, duration kept
    cells = []
    for other in range(len(table.rows[row])):
        text = table.cell(row, other) or ""
        cell = parse_number(text) if other != col and not re.search(r"[A-Za-z]", text) else None  # not the label's "(1)"
        if cell is not None and column_header(table, first, other) == period:
            cells.append(cell.value * factor)
    return cells


_NO_XBRL = {"version": 1, "parts": {}}


def _chain_of(day: date) -> int:
    """Which reading chain a quarter belongs to: quarters ending in the same months of the year share one."""
    return day.month % 12 // 3


def _same_chain(period_end: str, day: date) -> bool:
    return _chain_of(date.fromisoformat(period_end[:10])) == _chain_of(day)


def _folder(ref: FilingRef) -> str:
    return filing_folder(ref.filed, ref.form, ref.accession)


def _period_headed(document: Document, locator: dict) -> bool:
    """Whether a value's column is headed by words naming its period ("three months ended", "Q4"), so the row's
    other cells under the same words are the same length of period."""
    table = document.tables.get(str(locator.get("table")))
    if table is None or locator.get("col") is None:
        return False
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0])), 1)
    return bool(_PERIOD_HEADER.search(column_header(table, first, int(locator["col"]))))


_PERIOD_HEADER = re.compile(r"months?|weeks?|quarter|year|\bq[1-4]\b|\bfy\b|@", re.I)


def _identity_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _proven(value: dict) -> bool:
    # A line replaced by its report's exact tagged figure (Adobe's customer groups) is a figure reported in XBRL.
    return any(note.startswith(_PROOFS) or _EXACT_NOTE.match(note) for note in value.get("notes") or [])


def _rounding(value: float) -> float:
    """Half the step a stated figure is rounded to ($5.96 billion: 5 million)."""
    whole, step = abs(int(round(value))), 1
    while whole and whole % (step * 10) == 0 and step < 10 ** 12:
        step *= 10
    return step / 2


def _significant(value: float) -> bool:
    """A figure precise enough that equality with another is no coincidence (12,345,000,000, not 60,000,000,000)."""
    digits = f"{abs(value):.0f}".rstrip("0")
    return abs(value) >= 1e6 and len(digits) >= 3


def _keeps_verified(existing: dict, incoming: dict) -> bool:
    # A figure read from a slide's chart is kept too against the same filing's unchecked reading of its text.
    return (existing["validation_status"] == "verified" and incoming["validation_status"] == "needs_review"
            and (existing.get("source_accession") != incoming.get("source_accession") or existing.get("method") == CHART_METHOD))


def _unreported(spec: Spec, read: list[ReadValue]) -> list[str]:
    """The listed KPIs a document did not report, apart from breakdowns read as whole tables (their rows vary)."""
    whole = {item.group.key for item in read if item.locator.get("whole_table")}
    found = {f"{item.group.key}.{item.kpi.key}" for item in read}
    return [f"{group.key}.{kpi.key}" for group in spec.groups if group.key not in whole for kpi in group.kpis
            if kpi.key != group.total_kpi and f"{group.key}.{kpi.key}" not in found]


def _dimension(label: str) -> str:
    """What a breakdown splits revenue by: "Revenue by Technology Mix" and "Revenue by Technology" → "technology"."""
    words = re.sub(r"\b(wafer|net|revenue|sales|by|mix|share|shares|of|the)\b", " ", label.lower())
    return " ".join(words.split())


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= max(abs(left), abs(right)) * 1e-9


_PERIOD_WORDS = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                           r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|first|second|third|fourth|q[1-4])\b")


def _template(text: str) -> str:
    """A quote with its numbers and period words blanked: "DAP was 3.60 billion on average for June 2026" and the
    same sentence for September share one template."""
    return re.sub(r"[\d.,]+", "#", _PERIOD_WORDS.sub("@", clean(text).lower()))


def _same_place(now: dict, before: dict) -> bool:
    """Whether a KPI was found where it was last quarter: the same table row label, or the same sentence shape."""
    if "quote" in now and "quote" in before:
        return _template(now["quote"]) == _template(before["quote"])
    if "row_label" in now and "row_label" in before:
        return clean(now["row_label"]).lower() == clean(before["row_label"]).lower() and bool(now["row_label"])
    return False


def _source_template(url: str) -> str:
    """A document's kind from its file name with the dates and numbers taken out ("a2q26e_withguidance.htm")."""
    return re.sub(r"\d+", "", url.rsplit("/", 1)[-1].lower())


def _fetch_image(link: str) -> bytes:
    request = urllib.request.Request(link, headers={"User-Agent": os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com")})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def release_reread_due(previous: dict | None) -> bool:
    """Whether a finished release should be read again, once, by the reader that fixes what an older one missed:
    * reader 1 reads every tracked group: a release an earlier stamped reader left with a group unread (one read
      before stamping began is left alone);
    * reader 2 reads a quote from a table laid out as text (Meta's highlight bullets): a release where a reading was
      dropped as "unknown text block", whatever came of it."""
    if not previous or previous.get("status") not in _DONE:
        return False
    notes = previous.get("notes") or []
    stamp = next((note for note in notes if note.startswith(_RELEASE_READER_PREFIX)), None)
    version = int(stamp.removeprefix(_RELEASE_READER_PREFIX)) if stamp and stamp.removeprefix(_RELEASE_READER_PREFIX).isdigit() else None
    if (version is None or version < 2) and any("unknown text block" in note for note in notes):
        return True
    return (previous.get("status") == "processed" and version is not None and version < 1
            and any(note.startswith(_NOT_REPORTED) for note in notes))


_INVALID_POINTER = re.compile(r"unknown (table|text block)|quote not found|not inside the quote|no number in|combined line")


def _explain_pointer(problem: str, document: Document) -> str:
    """Retry feedback that names the fix: a text block cited as a table (ASML's statements are slide images whose
    figures come through as text) must be quoted instead."""
    match = re.search(r"unknown table (\S+)", problem)
    if match and match.group(1) in document.blocks:
        block = match.group(1)
        return (f"{problem}: {block} is a text block, not a table. Cite it as "
                f'{{"kpi": ..., "block": "{block}", "quote": "<the label and the numbers, copied exactly>", '
                f'"value_text": "<the one number for the reported period>"}}; the column headers at the start of the '
                "block give the order of the periods.")
    if match:
        return f"{problem}: the document's tables are {', '.join(list(document.tables)[:40]) or 'none'}"
    return problem


def _is_jump(note: str) -> bool:
    return note.startswith(("changed ", "moved "))


def _document_text(document: Document) -> str:
    cells = [" ".join(cell for row in table.rows for cell in row) for table in document.tables.values()]
    return clean(" ".join([*(block.text for block in document.blocks.values()), *cells]))


def _days(left: str, right: str) -> int:
    return (date.fromisoformat(left[:10]) - date.fromisoformat(right[:10])).days


def _row_label(locator: dict) -> str | None:
    """The name a reading's row goes by, its footnote mark dropped: "Lithography systems sold (units) 1 132 77 …" is
    "Lithography systems sold (units)"."""
    label = locator.get("row_label") or (re.split(r"\s\$?\(?[\d,.]+\)?(?:\s|$)", clean(locator["quote"]), maxsplit=1)[0]
                                         if locator.get("quote") else None)
    label = re.sub(r"(?:\s+\d{1,2})+$", "", clean(label or ""))
    return label if len(label) >= 8 else None


def own_label(ref: FilingRef, own: dict) -> str:
    quarter = next((quarter for quarter, other in own.items() if other is ref), None)
    return f"{quarter[1]} {quarter[0]}" if quarter else ref.accession


def _row_named(document: Document, value: float, others: set[str]) -> str | None:
    """The label of a table row whose first amount is `value`, in a table that lists at least two of `others`."""
    for table in document.tables.values():
        scale = table.declared_scale() or document.default_scale()
        labels = [clean(row[0]) if row else "" for row in table.rows]
        if not scale or len({line_key(label) for label in labels if label} & others) < 2:
            continue
        for row, label in zip(table.rows, labels):
            if not label or line_key(label) in others:
                continue
            amount = next((parsed for cell in row[1:] if (parsed := parse_number(cell)) is not None), None)
            if amount is not None and not amount.percent and abs(amount.value * scale - value) <= scale / 2:
                return label
    return None
