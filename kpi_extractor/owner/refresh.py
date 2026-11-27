"""The owner-locked refresh: what a new filing adds to an owner-built company (business_kpi_specs.model = 'owner').

The owner's decision (2026-11-25): keep the refresh running, but a new filing only fills its new period into the lines
that already exist; every existing period stays as the owner left it. So, for each new filing:
- Its period is the quarter after the newest stored one (or that newest quarter itself, for a 10-Q following its release).
  Anything else (a skipped quarter) goes to the owner's hand pass.
- A period holding any owner-decided row is read-only: nothing is inserted, updated or re-flagged in it. A later filing
  that gives a figure differently, or gives one the owner left out, becomes a hand-reading entry instead.
- Each line is read only from its stored source (owner/sources.py), by code; no AI is ever called. A line without a
  source, or one only a person can read (a figure in prose), goes to the hand-reading queue.
- A figure is verified only when its source gives exactly one figure and its breakdown adds up to its total; otherwise it
  is held with the reason. A basis change (a new member, a mapped line missing from a breakdown, parts that no longer
  add up, the table gone or rescaled) builds nothing for that table: it goes to the hand pass.
- A line missing from the filing is flagged, never marked N/A. Only the newest period's release figures may be replaced,
  by its 10-Q/10-K where the two differ, with a note.
- A 10-K adds the year's FY rows and Q4 = FY less the Q3 report's nine months; a balance (backlog) is read at year end.
- A foreign filer's interim 6-K report is its quarter's report (a line's `interim` source, else its `report`), and
  its Q3 one gives Q4's nine months. A line read only from a report waits for it only where one will come (a 10-Q
  after its 8-K, a 20-F after the fourth quarter's 6-K, a filer's interim 6-K report); otherwise it is read by hand.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Callable

from ..documents.document import Document
from ..sources.base import FilingRef
from .resolve import MANY, NONE, ONE, Fact, Found, new_members, resolve_table, resolve_xbrl
from .sources import LineMapping, LineRef, OwnerSources, TableSource, XbrlSource, parse_sources

OWNER_MODEL = "owner"
CODE_NOTE = "read by code from the owner's line sources (no AI)"
_DONE = {"processed", "needs_review", "skipped"}
_SAME_PERIOD = timedelta(days=10)
_NEXT_QUARTER = (60, 125)  # days from one quarter's end to the next's
_CURRENT = timedelta(days=200)  # a line with no figure this long before the newest quarter has stopped reporting
_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


def is_owner_spec(spec: dict | None) -> bool:
    return bool(spec) and spec.get("model") == OWNER_MODEL


@dataclass
class HandRead:
    """One entry in the owner's "new quarter to read by hand" queue (business_kpi_hand_reads)."""
    group_key: str
    kpi_key: str  # "" for a whole table (a basis change)
    fiscal_year: str
    fiscal_period: str
    period_end: str | None
    kind: str  # hand | unmapped | missing | basis | differs | gap
    reason: str
    accession: str | None = None
    source_url: str | None = None

    def record(self) -> dict:
        return {**asdict(self), "reason": self.reason[:500]}


@dataclass
class FilingOutcome:
    ref: FilingRef
    status: str
    notes: list[str]
    fiscal_year: str | None = None
    fiscal_period: str | None = None
    period_end: date | None = None


@dataclass
class OwnerPlan:
    rows: list[dict] = field(default_factory=list)
    hand: list[HandRead] = field(default_factory=list)
    filings: list[FilingOutcome] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Period:
    fiscal_year: str
    fiscal_period: str
    end: date

    @property
    def label(self) -> str:
        return f"{self.fiscal_period} {self.fiscal_year}" if self.fiscal_period != "FY" else f"FY {self.fiscal_year}"

    def following(self, end: date) -> "Period":
        index = _QUARTERS.index(self.fiscal_period)
        if index == 3:
            return Period(str(int(self.fiscal_year) + 1), "Q1", end)
        return Period(self.fiscal_year, _QUARTERS[index + 1], end)


@dataclass
class OwnerLine:
    group: str
    kpi: str
    template: dict  # its newest owner-decided row: labels, order, unit and currency as the owner left them
    last_end: date

    @property
    def key(self) -> str:
        return f"{self.group}.{self.kpi}"

    @property
    def breakdown(self) -> bool:
        return self.template.get("group_kind") == "revenue_breakdown"


@dataclass
class Reading:
    found: Found
    origin: str  # release | report | derived
    ref: FilingRef | None
    where: str
    scale: float = 1.0


@dataclass
class _Docs:
    """The filings one period is read from."""
    release: FilingRef | None = None
    report: FilingRef | None = None
    nine_months: FilingRef | None = None  # the year's Q3 report, for Q4 = FY less nine months
    # Whether the period's report is still to come when only its release is here: a U.S. filer's 10-Q or 10-K, a
    # foreign filer's 20-F after its fourth quarter or its interim 6-K report; not a foreign filer's other quarters.
    report_expected: bool = True

    @property
    def annual(self) -> bool:
        return self.report is not None and self.report.form.upper().startswith(("10-K", "20-F", "40-F"))

    def newest(self) -> FilingRef:
        return self.report or self.release  # type: ignore[return-value]


def _day(text) -> date:
    return date.fromisoformat(str(text)[:10])


def is_interim(ref: FilingRef | None) -> bool:
    """A foreign filer's interim report: a 6-K read as its quarter's report (owner/foreign.py)."""
    return ref is not None and ref.role == "periodic_report" and ref.form.upper() == "6-K"


def report_source(mapping: LineMapping, ref: FilingRef | None):
    """The line's source in this report: its `interim` one in an interim 6-K report, where it has one."""
    return mapping.interim if is_interim(ref) and mapping.interim is not None else mapping.report


def differs(a: float, b: float) -> bool:
    return abs(a - b) > max(1.0, 1e-4 * max(abs(a), abs(b)))


class OwnerRefresh:
    """Plans the rows, hand-reading entries and filing records one run adds for an owner-built company. Reads nothing
    itself: `facts` and `document` give a filing's XBRL facts and parsed document (None when unreadable)."""

    def __init__(self, symbol: str, spec: dict, values: list[dict], filings: dict, *,
                 facts: Callable[[FilingRef], list[Fact] | None], document: Callable[[FilingRef], Document | None],
                 rejected: set[tuple] = frozenset()):
        self.symbol, self.spec, self.filings, self.rejected = symbol, spec, filings, set(rejected)
        self.values = [v for v in values if v.get("validation_status") != "rejected"]
        self.sources, self.sources_problem = parse_sources(spec.get("sources"))
        self._facts, self._document = facts, document
        # Groups whose owner rows carry the total they add up to: a new figure there stands only once tied to one.
        self._totalled = {v["group_key"] for v in self.values if v.get("owner_decision")
                          and isinstance(v.get("locator"), dict) and v["locator"].get("total") is not None}
        self._fact_cache: dict[str, list[Fact] | None] = {}
        self._doc_cache: dict[str, Document | None] = {}

    # --- what the owner built ---------------------------------------------------------------------------------------

    def lines(self, annual: bool = False) -> dict[str, OwnerLine]:
        """The owner's lines: every (group, KPI) with an owner-decided quarter (or, `annual`, with owner-decided FY rows and
        no quarters: Dell's revenue by country, which only its 10-K gives). Nothing else is ever written to."""
        quarterly = {f"{v['group_key']}.{v['kpi_key']}" for v in self.values if v.get("owner_decision")
                     and v["fiscal_period"] != "FY"}
        lines: dict[str, OwnerLine] = {}
        for value in self.values:
            key = f"{value['group_key']}.{value['kpi_key']}"
            if not value.get("owner_decision") or (value["fiscal_period"] == "FY") != annual or (annual and key in quarterly):
                continue
            end = _day(value["period_end"])
            if key not in lines or end > lines[key].last_end:
                lines[key] = OwnerLine(value["group_key"], value["kpi_key"], dict(value), end)
        return lines

    def current_lines(self) -> dict[str, OwnerLine]:
        """The owner's lines still reporting: a figure within about two quarters of the newest (Dell's VMware, spun off in
        2021, is a line of the history, not one a new filing is read for)."""
        lines = self.lines()
        if not lines:
            return {}
        newest = max(line.last_end for line in lines.values())
        return {key: line for key, line in lines.items() if line.last_end >= newest - _CURRENT}

    def _rows_in(self, fiscal_year: str, fiscal_period: str, lines: dict[str, OwnerLine]) -> dict[str, dict]:
        return {f"{v['group_key']}.{v['kpi_key']}": v for v in self.values if v["fiscal_year"] == fiscal_year
                and v["fiscal_period"] == fiscal_period and f"{v['group_key']}.{v['kpi_key']}" in lines}

    def _latest(self, lines: dict[str, OwnerLine]) -> Period | None:
        quarters = [v for v in self.values if v["fiscal_period"] in _QUARTERS and f"{v['group_key']}.{v['kpi_key']}" in lines]
        if not quarters:
            return None
        newest = max(quarters, key=lambda v: (_day(v["period_end"]), v["fiscal_year"], v["fiscal_period"]))
        return Period(str(newest["fiscal_year"]), newest["fiscal_period"], _day(newest["period_end"]))

    def _pending(self, ref: FilingRef) -> bool:
        stored = self.filings.get(ref.accession)
        return not stored or stored.get("status") not in _DONE

    # --- the run ----------------------------------------------------------------------------------------------------

    def plan(self, reports: list[FilingRef], releases: list[FilingRef]) -> OwnerPlan:
        plan = OwnerPlan()
        lines = self.lines()
        latest = self._latest(lines)
        if self.sources_problem:
            plan.log.append(f"{self.symbol}: {self.sources_problem}; every line goes to the hand-reading queue")
        if latest is None:
            plan.log.append(f"{self.symbol}: owner-built list with no owner-decided quarter; nothing to add to")
            return plan
        current = self.current_lines()
        candidates = sorted((ref for ref in [*releases, *reports] if ref.period_end and self._pending(ref)),
                            key=lambda ref: (ref.period_end, ref.role != "earnings_release", ref.filed))
        clusters: list[list[FilingRef]] = []
        for ref in candidates:
            if clusters and abs(ref.period_end - clusters[-1][0].period_end) <= _SAME_PERIOD:
                clusters[-1].append(ref)
            else:
                clusters.append([ref])
        for cluster in clusters:
            docs = _Docs(release=next((r for r in reversed(cluster) if r.role == "earnings_release"), None),
                         report=next((r for r in reversed(cluster) if r.role == "periodic_report"), None))
            end = docs.newest().period_end
            if end < latest.end - _SAME_PERIOD:
                for ref in cluster:
                    plan.filings.append(FilingOutcome(ref, "processed", [
                        "owner-built period: the owner-locked refresh leaves it as the owner built it"], period_end=ref.period_end))
                continue
            if abs(end - latest.end) <= _SAME_PERIOD:
                period = Period(latest.fiscal_year, latest.fiscal_period, latest.end)
            elif _NEXT_QUARTER[0] <= (end - latest.end).days <= _NEXT_QUARTER[1]:
                period = latest.following(end)
            else:
                reason = (f"the filing's period ends {end.isoformat()}, not the quarter after {latest.label} "
                          f"({latest.end.isoformat()}): a quarter is missing or the fiscal calendar changed")
                plan.hand.append(HandRead("", "", "", "", end.isoformat(), "gap", reason, docs.newest().accession,
                                          docs.newest().source_url))
                for ref in cluster:
                    plan.filings.append(FilingOutcome(ref, "needs_review", [f"owner-locked refresh: {reason}"], period_end=end))
                continue
            if docs.report is None and docs.release is not None and docs.release.form.upper() == "6-K":
                docs.report_expected = period.fiscal_period == "Q4" or any(is_interim(r) for r in reports)
            if period.fiscal_period == "Q4" and docs.annual:
                q3 = self._rows_in(period.fiscal_year, "Q3", lines)
                q3_end = max((_day(v["period_end"]) for v in q3.values()), default=None)
                docs.nine_months = next((r for r in reports if r.role == "periodic_report" and q3_end and r.period_end
                                         and abs(r.period_end - q3_end) <= _SAME_PERIOD), None)
            self._period(plan, period, docs, current)
            if period.end > latest.end:
                latest = period
        return plan

    def _period(self, plan: OwnerPlan, period: Period, docs: _Docs, lines: dict[str, OwnerLine]) -> None:
        start = (len(plan.rows), len(plan.hand))
        existing = self._rows_in(period.fiscal_year, period.fiscal_period, lines)
        readonly = any(row.get("owner_decision") for row in existing.values())
        readings = {key: self._quarter(line, period, docs) for key, line in lines.items()}
        if docs.report is None:
            # A release's own period end is its columns' date (the listing guesses one: Dell's Q3 FY2027, October 29 for 30).
            dates = [r.found.locator["column_date"] for got in readings.values() for r in got if r.found.locator.get("column_date")]
            if dates:
                period = Period(period.fiscal_year, period.fiscal_period, _day(max(set(dates), key=dates.count)))
        waiting = self._settle(plan, period, readings, lines, existing, readonly, docs, "quarter")
        if docs.annual and docs.report.period_end:
            year = Period(period.fiscal_year, "FY", docs.report.period_end)
            fy_existing = self._rows_in(year.fiscal_year, "FY", lines)
            annual = {key: line for key, line in self.lines(annual=True).items()
                      if line.last_end >= year.end - timedelta(days=400)}
            fy_lines = {key: line for key, line in {**lines, **annual}.items() if self.sources.group(line.group).fy_rows
                        and not (self.sources.line(line.group, line.kpi) or LineMapping()).balance
                        and ((self.sources.line(line.group, line.kpi) or LineMapping()).report is not None or key in annual)}
            fy_readings = {key: self._year(line, year, docs) for key, line in fy_lines.items()}
            self._settle(plan, year, fy_readings, fy_lines, fy_existing,
                         any(row.get("owner_decision") for row in fy_existing.values()), docs, "fy", quiet=set(lines))
        rows, hand = len(plan.rows) - start[0], len(plan.hand) - start[1]
        held = sum(1 for row in plan.rows[start[0]:] if row["validation_status"] == "needs_review")
        notes = [f"owner-locked refresh, {period.label}: {rows} figures written ({held} held), {hand} to read by hand"]
        if readonly:
            notes.append("the period is owner-decided: nothing in it was changed")
        if waiting:
            notes.append(f"{len(waiting)} lines wait for the {period.label} report: {', '.join(sorted(waiting))}")
        refs = [ref for ref in (docs.release, docs.report) if ref is not None]
        for ref in refs:
            plan.filings.append(FilingOutcome(ref, "needs_review" if hand or held else "processed", notes,
                                              period.fiscal_year, period.fiscal_period, period.end))
        plan.log.append(f"{self.symbol}: {notes[0]}")

    # --- reading one line -------------------------------------------------------------------------------------------

    def _facts_of(self, ref: FilingRef) -> list[Fact] | None:
        if ref.accession not in self._fact_cache:
            self._fact_cache[ref.accession] = self._facts(ref)
        return self._fact_cache[ref.accession]

    def _doc_of(self, ref: FilingRef) -> Document | None:
        if ref.accession not in self._doc_cache:
            self._doc_cache[ref.accession] = self._document(ref)
        return self._doc_cache[ref.accession]

    def _read(self, source, ref: FilingRef, end: date, duration: str, origin: str) -> Reading:
        if isinstance(source, XbrlSource):
            facts = self._facts_of(ref)
            found = (Found(NONE, reason="the report has no XBRL", basis=True) if facts is None
                     else resolve_xbrl(facts, source, end, duration))
            where = f"{ref.form} {ref.accession} XBRL {'/'.join(source.concepts)} [{', '.join(source.members.values()) or 'no dimension'}]"
            return Reading(found, origin, ref, where, float(found.locator.get("precision") or 1.0))
        found = resolve_table(self._doc_of(ref), source, end, duration)
        where = (f"{ref.form} {ref.accession}, table under {source.anchor!r}, row "
                 f"{found.locator.get('row_label') or source.row!r}, column {found.locator.get('header') or '?'!r}")
        return Reading(found, origin, ref, where, source.scale)

    def _quarter(self, line: OwnerLine, period: Period, docs: _Docs) -> list[Reading]:
        """The line's readings for the quarter, the filing of record (the report) last."""
        mapping = self.sources.line(line.group, line.kpi)
        if mapping is None:
            return []
        out = []
        if docs.release is not None and mapping.release is not None:
            out.append(self._read(mapping.release, docs.release, period.end, "quarter", "release"))
        source = report_source(mapping, docs.report)
        if docs.report is not None and source is not None:
            balance = mapping.balance or getattr(source, "instant", False)
            if not docs.annual or balance:
                out.append(self._read(source, docs.report, period.end, "quarter", "report"))
            else:
                out.append(self._fourth(mapping, period, docs))
        return out

    def _fourth(self, mapping: LineMapping, period: Period, docs: _Docs) -> Reading:
        """Q4 = the 10-K's year less the Q3 report's nine months (a foreign filer's: its Q3 interim 6-K report's)."""
        year = self._read(mapping.report, docs.report, docs.report.period_end, "fy", "report")
        nine_source = report_source(mapping, docs.nine_months) if docs.nine_months is not None else None
        if docs.nine_months is None or nine_source is None:
            return Reading(Found(NONE, reason="the year's Q3 report is not listed, so its nine months cannot be read"),
                           "derived", docs.report, year.where)
        nine = self._read(nine_source, docs.nine_months, docs.nine_months.period_end, "ytd", "report")
        where = f"{year.where} less nine months from {nine.where}"
        if year.found.status != ONE or nine.found.status != ONE:
            bad = year.found if year.found.status != ONE else nine.found
            return Reading(Found(bad.status, reason=f"Q4 not derived: {bad.reason}", basis=bad.basis), "derived", docs.report, where)
        value = round(year.found.value - nine.found.value, 2)
        return Reading(Found(ONE, value, locator={"fy": year.found.value, "nine_months": nine.found.value,
                                                  "fy_source": year.found.locator, "nine_months_source": nine.found.locator}),
                       "derived", docs.report, where, year.scale)

    def _year(self, line: OwnerLine, year: Period, docs: _Docs) -> list[Reading]:
        mapping = self.sources.line(line.group, line.kpi)
        if mapping is None or mapping.report is None:
            return []
        return [self._read(mapping.report, docs.report, year.end, "fy", "report")]

    def _total(self, group: str, origin: str, period: Period, docs: _Docs, chosen: dict[str, Reading],
               duration: str) -> Reading | None:
        mapping = self.sources.group(group)
        source = mapping.report_total if origin in ("report", "derived") else mapping.release_total
        if origin == "report" and is_interim(docs.report) and mapping.interim_total is not None:
            source = mapping.interim_total
        if source is None:
            return None
        if isinstance(source, LineRef):
            return chosen.get(source.line)
        ref = docs.release if origin == "release" else docs.report
        if ref is None:
            return None
        if origin == "derived":
            nine = mapping.interim_total if not isinstance(mapping.interim_total, LineRef) else None
            return self._fourth(LineMapping(report=source, interim=nine), period, docs)
        return self._read(source, ref, period.end if duration == "quarter" else ref.period_end, duration, origin)

    # --- settling a period ------------------------------------------------------------------------------------------

    def _settle(self, plan: OwnerPlan, period: Period, readings: dict[str, list[Reading]], lines: dict[str, OwnerLine],
                existing: dict[str, dict], readonly: bool, docs: _Docs, duration: str, quiet: set[str] = frozenset()) -> set[str]:
        """Settles the period's lines; returns those waiting for its report."""
        def hand(group, kpi, kind, reason, ref=None):
            ref = ref or docs.newest()
            plan.hand.append(HandRead(group, kpi, period.fiscal_year, period.fiscal_period, period.end.isoformat(),
                                      kind, reason, ref.accession, ref.source_url))

        chosen: dict[str, Reading] = {}
        missing: dict[str, tuple[str, bool]] = {}  # why, and whether it is the document's (a basis change)
        waiting: set[str] = set()  # lines whose source is in a filing not here yet (the report after its release)
        unread: set[str] = set()  # lines whose only source is a report that never comes for this period: read by hand
        for key, line in lines.items():
            mapping = self.sources.line(line.group, line.kpi)
            if mapping is None or (mapping.release is None and mapping.report is None and mapping.interim is None):
                # A quarterly line's own quarter queues it once; its FY row then waits with it.
                if not readonly and key not in existing and key not in quiet:
                    reason = mapping.hand if mapping and mapping.hand else "no source is mapped for this line"
                    hand(line.group, line.kpi, "hand" if mapping and mapping.hand else "unmapped",
                         f"{reason}: read {line.template.get('kpi_label') or line.kpi} for {period.label} by hand")
                continue
            got = readings.get(key) or []
            usable = [reading for reading in got if reading.found.status != NONE]
            if usable:
                chosen[key] = usable[-1]  # the report, the filing of record, over its release
            elif got:
                missing[key] = ("; ".join(r.found.reason for r in got), any(r.found.basis for r in got))
            elif docs.report is None and not docs.report_expected:
                # Read only from a report, and none comes for this quarter (a foreign filer's results 6-K alone): not left
                # waiting where nothing would ever read it.
                unread.add(key)
                if not readonly and key not in existing and key not in quiet:
                    hand(line.group, line.kpi, "hand", f"its source is the quarter's report, and none is filed for "
                         f"{period.label}: read {line.template.get('kpi_label') or line.kpi} by hand")
            else:
                waiting.add(key)
        for group in sorted({line.group for line in lines.values()}):
            members = {key: line for key, line in lines.items() if line.group == group
                       and (key in chosen or key in missing or key in waiting or key in unread)}
            if any(key in chosen or key in missing for key in members):
                self._group(plan, period, group, members, chosen, missing, waiting, existing, readonly, docs, duration, hand,
                            readings, unread)
        return waiting

    def _group(self, plan, period, group, members, chosen, missing, waiting, existing, readonly, docs, duration, hand,
               readings_of, unread=frozenset()) -> None:
        breakdown = next(iter(members.values())).breakdown
        basis = [f"{key}: {chosen[key].found.reason}" for key in members if key in chosen and chosen[key].found.basis]
        basis += [f"{key}: {missing[key][0]}" for key in members if key in missing and missing[key][1]]
        if breakdown:
            basis += [f"{key} is missing from the filing ({missing[key][0]})" for key in members
                      if key in missing and not missing[key][1]]
        if basis:
            if not readonly:
                hand(group, "", "basis", f"basis change, nothing built for {group} in {period.label}: " + "; ".join(basis))
            return
        parts = [chosen[key] for key in members if key in chosen]
        origin = parts[0].origin if parts else "report"
        tie, held_why = None, None
        if any(reading.found.status == MANY for reading in parts):
            held_why = "held: a line of this table matches more than one figure, so the table is not tied"
        elif waiting & set(members):
            held_why = (f"held: {', '.join(sorted(waiting & set(members)))} come with the report, so the lines are tied "
                        "when it is read")
        elif unread & set(members):
            held_why = (f"held: {', '.join(sorted(unread & set(members)))} are read by hand, so the lines are not tied "
                        "until they are")
        else:
            total = self._total(group, origin, period, docs, chosen, duration)
            # Members no line maps are a basis change only where nothing else proves the lines whole: an owner list is
            # often a chosen part of what the filer tags (AbbVie's products by region), and lines that add up to their
            # total leave nothing out.
            new = self._new_members(group, members, docs, period, duration) if total is None else []
            if new:
                if not readonly:
                    hand(group, "", "basis", f"basis change, nothing built for {group} in {period.label}: the filing tags "
                         f"members no line maps, and no total ties the lines: {', '.join(new[:6])}")
                return
            if total is None:
                held_why = ("held: no total is mapped for this breakdown, so its lines cannot be tied"
                            if breakdown and group in self._totalled else None)
            elif total.found.status != ONE:
                held_why = f"held: the total could not be read ({total.found.reason}), so the lines are not tied"
            else:
                summed = sum(reading.found.value for reading in parts)
                scale = max([reading.scale for reading in parts] + [total.scale])
                tolerance = max(0.51 * scale * (len(parts) + 1), 1e-6 * abs(total.found.value))
                if abs(summed - total.found.value) > tolerance:
                    if not readonly:
                        hand(group, "", "basis", f"basis change, nothing built for {group} in {period.label}: the lines "
                             f"add up to {summed:,.0f}, the total is {total.found.value:,.0f}")
                    return
                tie = total.found.value
        for key, line in members.items():
            if key in waiting or key in unread:
                continue
            label = line.template.get("kpi_label") or line.kpi
            stored = existing.get(key)
            if key in missing:
                if not readonly and stored is None:
                    hand(line.group, line.kpi, "missing", f"{label} is not in the filing where its source points "
                         f"({missing[key][0]}); read it by hand or mark it not reported")
                continue
            reading = chosen[key]
            line_held = held_why
            if tie is None and line_held is None:
                # With no total to tie to, a release and a report that disagree prove neither.
                others = [r for r in readings_of.get(key, []) if r is not reading and r.found.status == ONE]
                if reading.found.status == ONE and any(differs(r.found.value, reading.found.value) for r in others):
                    line_held = (f"held: the release gives {others[0].found.value:,.0f}, the report "
                                 f"{reading.found.value:,.0f}, and no total ties either")
            if readonly or (stored and stored.get("owner_decision")):
                if reading.found.status == ONE and stored is not None and differs(reading.found.value, float(stored["value"])):
                    hand(line.group, line.kpi, "differs", f"{reading.where} gives {reading.found.value:,.0f}; the "
                         f"owner-decided figure is {float(stored['value']):,.0f}", reading.ref)
                elif reading.found.status == ONE and stored is None:
                    hand(line.group, line.kpi, "missing", f"the owner-decided {period.label} has no figure for {label}; "
                         f"{reading.where} gives {reading.found.value:,.0f}", reading.ref)
                continue
            if stored is not None and not self._gives_way(stored, reading, docs):
                continue
            if (line.group, line.kpi, period.fiscal_year, period.fiscal_period) in self.rejected:
                hand(line.group, line.kpi, "differs", f"the owner rejected the {period.label} figure for {label} before; "
                     f"{reading.where} gives {reading.found.value if reading.found.value is not None else '?'}", reading.ref)
                continue
            plan.rows.append(self._row(line, period, reading, stored, tie, line_held))

    def _gives_way(self, stored: dict, reading: Reading, docs: _Docs) -> bool:
        """Whether a figure this refresh stored earlier for the newest period gives way to this reading: only a release's,
        to its report, where the two differ, or where the report ties a figure the release left held."""
        release = (self.filings.get(stored.get("source_accession")) or {}).get("document_role") == "earnings_release" or (
            docs.release is not None and stored.get("source_accession") == docs.release.accession)
        if not (release and reading.origin in ("report", "derived") and reading.found.status == ONE):
            return False
        return differs(reading.found.value, float(stored["value"])) or stored.get("validation_status") == "needs_review"

    def _new_members(self, group, members, docs, period, duration) -> list[str]:
        if docs.report is None:
            return []
        sources = [source for key in members if (m := self.sources.lines.get(key))
                   and isinstance(source := report_source(m, docs.report), XbrlSource) and not source.instant]
        facts = self._facts_of(docs.report) if sources else None
        if not facts:
            return []
        span = "fy" if docs.annual else "quarter"
        end = docs.report.period_end if docs.annual else period.end
        return new_members(facts, sources, self.sources.group(group).known_members, end, span)

    def _row(self, line: OwnerLine, period: Period, reading: Reading, stored: dict | None, total: float | None,
             held_why: str | None) -> dict:
        found, template, ref = reading.found, line.template, reading.ref
        held = found.status == MANY or held_why is not None
        notes = [f"{CODE_NOTE}: {period.label} from {reading.where}"]
        if reading.origin == "derived":
            notes = [f"{CODE_NOTE}: Q4 {period.fiscal_year} = FY {period.fiscal_year} (10-K) less nine months (Q3 report): "
                     f"{reading.where}"]
        if found.status == MANY:
            notes.append(f"held: {found.reason}")
        elif held_why:
            notes.append(held_why)
        if stored is not None:
            notes.append(f"replaces the earnings release's {float(stored['value']):,.0f}: the report gives "
                         f"{found.value:,.0f}")
        value = found.value if found.status == ONE else found.candidates[0]
        method = "derived" if reading.origin == "derived" else "xbrl" if "XBRL" in reading.where else (
            template.get("method") if template.get("method") not in ("xbrl", "derived", None) else "ai")
        locator = {**found.locator, "source": "owner_sources", **({"total": total} if total is not None else {})}
        return {
            "symbol": self.symbol, "group_key": line.group, "kpi_key": line.kpi, "fiscal_year": period.fiscal_year,
            "fiscal_period": period.fiscal_period, "period_end": period.end.isoformat(),
            "group_label": template.get("group_label"), "group_kind": template.get("group_kind"),
            "group_order": template.get("group_order"), "kpi_label": template.get("kpi_label"),
            "kpi_order": template.get("kpi_order"), "unit": template.get("unit"), "currency": template.get("currency"),
            "value": value, "method": method, "validation_status": "needs_review" if held else "verified",
            "reconciliation_error_pct": None, "notes": notes, "source_accession": ref.accession,
            "source_url": ref.source_url, "filed_at": ref.filed.isoformat(), "locator": locator,
        }

