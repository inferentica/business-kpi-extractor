"""The gap finder: what a hand-built company's filings report that its owner tables do not carry, or carry differently.
It finds; it never writes a figure. No AI.

The refresh that wrote Dell's 418 rows on 2026-11-24 also found what the hand build had missed (Products/Services
revenue, revenue by country), beside a wrong Q4 (old-basis nine months taken from a new-basis year) and hundreds of
repeats. So finding is a job of its own, and every finding waits for the owner to check it against the filing:
- untracked: an axis's figures (a table) the owner tables do not carry at all;
- missing_line: a member of an owner table's own axes the table has no line for;
- missing_quarter: a quarter missing inside an owner line's stored run;
- differs: a filed figure for a stored line and period that is not the stored one;
- q4_basis: a 10-K's year and its Q3 report's nine months that do not carry the same lines, so Q4 = FY less nine months
  would mix bases.
A figure equal to one already stored for the same period, under any key, is a repeat and is never listed. Segment
income is the reportable segments only: operating income by any other member is not a finding.

    python -m kpi_extractor.owner.finder --state owner_state.json [--symbols DELL] --out findings.json [--sql findings.sql]

reads SEC only and writes the two files; nothing reaches the database until the owner runs the SQL.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..sources.base import FilingRef
from ..xbrl.model import REVENUE_CONCEPTS
from .derive import WRAPPERS
from .refresh import _QUARTERS, OwnerRefresh, _day, differs
from .resolve import ONE, Fact, _covers, resolve_xbrl
from .sources import XbrlSource, parse_sources

_REVENUE = {concept.split(":", 1)[-1] for concept in REVENUE_CONCEPTS}
_INCOME = "OperatingIncomeLoss"
# The axes an owner table is a split of; others (equity components, related parties) are not tables of figures served.
_TABLE_AXES = {"ProductOrServiceAxis", "StatementBusinessSegmentsAxis", "StatementGeographicalAxis"}
_SAME = timedelta(days=10)


@dataclass
class GapFinding:
    kind: str  # untracked | missing_line | missing_quarter | differs | q4_basis
    group_key: str
    kpi_key: str
    fiscal_year: str
    fiscal_period: str
    period_end: str | None
    concept: str = ""
    members: dict = field(default_factory=dict)
    value: float | None = None
    stored_value: float | None = None
    accession: str = ""
    source_url: str | None = None
    note: str = ""

    @property
    def members_key(self) -> str:
        return "+".join(f"{axis}={member}" for axis, member in sorted(self.members.items()))

    def record(self) -> dict:
        return {**asdict(self), "members_key": self.members_key, "note": self.note[:500]}


def _index(row: dict) -> int:
    return int(row["fiscal_year"]) * 4 + _QUARTERS.index(row["fiscal_period"])


def missing_quarters(values: list[dict], marks: list[dict] = ()) -> list[GapFinding]:
    """Quarters missing inside each owner line's stored run (between its first and last owner-decided quarter), less the
    ones the owner marked not reported (business_kpi_missing_marks; kpi_key '' marks the whole table)."""
    marked = {(m["group_key"], m.get("kpi_key") or "", str(m["fiscal_year"]), m["fiscal_period"]) for m in marks}
    runs: dict[tuple[str, str], dict[int, dict]] = {}
    for row in values:
        if row.get("owner_decision") and row.get("validation_status") != "rejected" and row["fiscal_period"] in _QUARTERS \
                and str(row["fiscal_year"]).isdigit():
            runs.setdefault((row["group_key"], row["kpi_key"]), {})[_index(row)] = row
    found = []
    for (group, kpi), rows in sorted(runs.items()):
        for index in range(min(rows), max(rows)):
            year, quarter = str(index // 4), _QUARTERS[index % 4]
            if index not in rows and not {(group, kpi, year, quarter), (group, "", year, quarter)} & marked:
                found.append(GapFinding("missing_quarter", group, kpi, str(index // 4), _QUARTERS[index % 4], None,
                                        note="a quarter missing inside the line's stored run"))
    return found


class GapFinder:
    """Findings for one owner-built company from its reports' XBRL. Reads the stored rows; writes nothing."""

    def __init__(self, symbol: str, spec: dict, values: list[dict]):
        self.symbol = symbol
        self.values = [v for v in values if v.get("validation_status") != "rejected"]
        self.sources, _ = parse_sources(spec.get("sources"))
        self.lines = {**OwnerRefresh(symbol, spec, values, {}, facts=lambda r: None, document=lambda r: None).lines(),
                      **OwnerRefresh(symbol, spec, values, {}, facts=lambda r: None, document=lambda r: None).lines(annual=True)}
        self.mapped = {key: m.report for key, m in self.sources.lines.items() if isinstance(m.report, XbrlSource)}
        self.ignore = set(WRAPPERS) | {member for source in self.mapped.values() for member in source.ignore}

    def _period(self, end: date, annual: bool) -> tuple[str, str]:
        for row in self.values:
            if abs(_day(row["period_end"]) - end) <= _SAME and (row["fiscal_period"] == "FY") == annual:
                return str(row["fiscal_year"]), row["fiscal_period"]
        return "", "FY" if annual else ""

    def _stored_values(self, end: date, annual: bool) -> set[float]:
        return {round(float(v["value"])) for v in self.values if abs(_day(v["period_end"]) - end) <= _SAME
                and (v["fiscal_period"] == "FY") == annual}

    def _tables(self) -> list[tuple[frozenset, frozenset, str]]:
        """Each owner table's axes, the members all its lines share, and its group."""
        groups: dict[str, list[XbrlSource]] = {}
        for key, source in self.mapped.items():
            groups.setdefault(key.split(".")[0], []).append(source)
        out = []
        for group, sources in groups.items():
            shapes = {frozenset(s.members) for s in sources if s.members}
            fixed = frozenset.intersection(*[frozenset(s.members.items()) for s in sources])
            out += [(shape, fixed, group) for shape in shapes]
        return out

    def find(self, report: FilingRef, facts: list[Fact] | None, nine_months: list[Fact] | None = None) -> list[GapFinding]:
        if not facts or report.period_end is None:
            return []
        annual = report.form.upper().startswith(("10-K", "20-F", "40-F"))
        end, duration = report.period_end, "fy" if annual else "quarter"
        year, period = self._period(end, annual)
        stored = self._stored_values(end, annual)
        mapped_sets = {frozenset(s.members.items()) for s in self.mapped.values()}
        tables = self._tables()
        base = dict(fiscal_year=year, fiscal_period=period, period_end=end.isoformat(), accession=report.accession,
                    source_url=report.source_url)
        found: list[GapFinding] = []
        seen: set[tuple] = set()
        for fact in facts:
            if fact.concept not in _REVENUE | {_INCOME} or not _covers(fact, end, duration, False):
                continue
            members = {axis: member for axis, member in fact.dims if member not in self.ignore}
            if not members or not set(members) <= _TABLE_AXES:
                continue
            if fact.concept == _INCOME and set(members) != {"StatementBusinessSegmentsAxis"}:
                continue  # segment income: the reportable segments only
            items = frozenset(members.items())
            mark = (fact.concept == _INCOME, items)
            if items in mapped_sets or mark in seen or round(fact.value) in stored:
                continue  # carried, already listed, or a repeat of a stored figure under another key
            seen.add(mark)
            owner = next((group for shape, fixed, group in tables if shape == frozenset(members) and fixed <= items), None)
            found.append(GapFinding("missing_line" if owner else "untracked", owner or "axis:" + "+".join(sorted(members)), "",
                                    concept=fact.concept, members=members, value=fact.value, **base,
                                    note=f"{report.form} tags {fact.concept} for {' + '.join(members.values())}"
                                         + (f", a member {owner} has no line for" if owner else ", a table no owner table carries")))
        found += self._differs(report, facts, end, duration, year, period, base)
        if annual and nine_months is not None:
            found += self._q4_basis(report, facts, nine_months, end, year, base)
        return found

    def _differs(self, report, facts, end, duration, year, period, base) -> list[GapFinding]:
        if not year:
            return []
        stored = {(v["group_key"], v["kpi_key"]): v for v in self.values
                  if str(v["fiscal_year"]) == year and v["fiscal_period"] == period}
        out = []
        for key, source in self.mapped.items():
            row = stored.get(tuple(key.split(".")))
            got = resolve_xbrl(facts, source, end, duration)
            if row is None or got.status != ONE or not differs(got.value, float(row["value"])):
                continue
            out.append(GapFinding("differs", *key.split("."), concept="/".join(source.concepts), members=source.members,
                                  value=got.value, stored_value=float(row["value"]), **base,
                                  note=f"{report.form} gives {got.value:,.0f}; stored {float(row['value']):,.0f}"))
        return out

    def _q4_basis(self, report, facts, nine_months, end, year, base) -> list[GapFinding]:
        """A 10-K's lines and its Q3 report's nine months must be the same lines, or FY less nine months mixes bases."""
        groups: dict[str, tuple[set, set]] = {}
        nine_end = max((f.end for f in nine_months if f.start and 250 <= (f.end - f.start).days <= 290), default=None)
        if nine_end is None:
            return []
        for key, source in self.mapped.items():
            group = key.split(".")[0]
            in_year = resolve_xbrl(facts, source, end, "fy").status == ONE
            in_nine = resolve_xbrl(nine_months, source, nine_end, "ytd").status == ONE
            have = groups.setdefault(group, (set(), set()))
            (have[0].add(key) if in_year else None), (have[1].add(key) if in_nine else None)
        out = []
        for group, (in_year, in_nine) in sorted(groups.items()):
            if in_year and in_nine and in_year != in_nine:
                out.append(GapFinding("q4_basis", group, "", **{**base, "fiscal_period": "Q4"},
                                      note=f"Q4 {year} = FY less nine months mixes bases: the 10-K has "
                                           f"{', '.join(sorted(in_year - in_nine)) or 'no extra line'}, the nine months "
                                           f"{', '.join(sorted(in_nine - in_year)) or 'no extra line'}"))
        return out


def run_findings(finder: GapFinder, reports: list[FilingRef], fresh: list[FilingRef], facts) -> list[GapFinding]:
    """The findings of a run's new reports; a 10-K is read beside its Q3 report's nine months."""
    found = []
    for report in fresh:
        nine = None
        if report.form.upper().startswith(("10-K", "20-F", "40-F")) and report.period_end:
            q3 = next((r for r in reports if r.role == "periodic_report" and r.period_end and r is not report
                       and 80 <= (report.period_end - r.period_end).days <= 100), None)
            nine = facts(q3) if q3 else None
        found += finder.find(report, facts(report), nine)
    return found


def sql_insert(company_id: str, symbol: str, findings: list[GapFinding]) -> str:
    rows = json.dumps([{**f.record(), "company_id": company_id, "symbol": symbol} for f in findings], separators=(",", ":"))
    tag = "gap_findings"
    assert f"${tag}$" not in rows
    return ("insert into public.business_kpi_gap_findings (company_id, symbol, kind, group_key, kpi_key, fiscal_year, "
            "fiscal_period, period_end, concept, members, members_key, value, stored_value, accession, source_url, note)\n"
            "select company_id, symbol, kind, group_key, kpi_key, fiscal_year, fiscal_period, period_end, concept, members, "
            "members_key, value, stored_value, accession, source_url, note\n"
            f"from jsonb_populate_recordset(null::public.business_kpi_gap_findings, ${tag}${rows}${tag}$::jsonb)\n"
            "on conflict do nothing;\n")


def main(argv: list[str] | None = None) -> int:
    from .backfill import Filings
    parser = argparse.ArgumentParser(description="Gap finder sweep over the hand-built companies (reads SEC only)")
    parser.add_argument("--state", required=True)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--quarters", type=int, default=8, help="recent reports to read per company")
    parser.add_argument("--out", default="findings.json")
    parser.add_argument("--sql", default=None)
    args = parser.parse_args(argv)
    state = json.loads(Path(args.state).read_text())
    wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    results, statements = {}, []
    for spec in sorted(state["specs"], key=lambda s: s["symbol"]):
        symbol = spec["symbol"]
        if spec.get("model") != "owner" or (wanted and symbol not in wanted):
            continue
        rows = [r for r in state["values"] if r["symbol"] == symbol]
        path = Path("owner_sources") / f"{symbol}.json"
        spec = {**spec, "sources": spec.get("sources") or (json.loads(path.read_text()) if path.exists() else {})}
        marks = [m for m in state.get("marks") or [] if m.get("symbol") == symbol]
        finder, found = GapFinder(symbol, spec, rows), missing_quarters(rows, marks)
        try:
            newest = max(_day(r["period_end"]) for r in rows)
            filings = Filings(symbol, newest - timedelta(days=92 * args.quarters + 60))
            reports = [r for r in filings.reports if r.period_end][-args.quarters:]
            for report in reports:
                nine = None
                if report.form.upper().startswith("10-K"):
                    q3 = next((r for r in reports if r.form.upper() in ("10-Q", "6-K") and r.period_end
                               and 80 <= (report.period_end - r.period_end).days <= 100), None)
                    nine = filings.facts(q3) if q3 else None
                found += finder.find(report, filings.facts(report), nine)
        except Exception as error:  # noqa: BLE001 - one company's failure is reported, the rest go on
            print(f"{symbol}: failed ({type(error).__name__}: {error})"[:300], flush=True)
        results[symbol] = [f.record() for f in found]
        print(f"{symbol}: {len(found)} findings " + json.dumps(
            {kind: sum(1 for f in found if f.kind == kind) for kind in sorted({f.kind for f in found})}), flush=True)
        if found and spec.get("company_id"):
            statements.append(sql_insert(spec["company_id"], symbol, found))
    Path(args.out).write_text(json.dumps(results, indent=1) + "\n")
    if args.sql:
        Path(args.sql).write_text("-- Gap findings (business_kpi_gap_findings). Run only with the owner's Allow.\nbegin;\n"
                                  + "".join(statements) + "commit;\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
