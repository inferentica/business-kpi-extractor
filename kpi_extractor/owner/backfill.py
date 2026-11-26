"""Backfill of the owner-built companies' line sources (business_kpi_specs.sources), written behind the owner's Allow.

    python -m kpi_extractor.owner.backfill --state owner_state.json [--symbols DELL,IBM] \
        --out owner_sources --sql owner_sources/backfill.sql

`owner_state.json` is a read-only export of the owner specs and their owner-decided rows (see docs/owner-locked-refresh.md
for the query). For each company the backfill reads its recent filings from SEC (no AI, no writes anywhere), proves each
line's source against the stored figures (owner/derive.py), then replays the newest quarter through the refresh itself
and compares what it would write with what the owner stored. It writes, per company, owner_sources/<SYMBOL>.json and a
report of the lines it could not map; and one SQL file of updates. Nothing reaches the database until the owner runs
that file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from ..documents.document import parse_document
from .foreign import plain_text, results_releases
from .derive import CHECKED, Evidence, LineResult, derive_line, derive_total, sources_from
from .refresh import OwnerRefresh, _day, differs
from .resolve import facts_of
from .sources import GroupMapping, parse_sources

_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


class Filings:
    """A company's recent filings from SEC, read once each."""

    def __init__(self, symbol: str, since, log=print):
        from ..sources import source
        from ..sources.sec import configure_identity
        configure_identity()
        self.source, self.log = source(), log
        self.company = self.source.open_company(symbol)
        self._facts: dict = {}
        self._documents: dict = {}
        self.reports = sorted(self.source.periodic_reports(self.company, since)
                              + self.source.interim_reports(self.company, since), key=lambda ref: ref.filed)
        for ref in self.reports:
            reported = getattr(ref.filing, "period_of_report", None)
            if ref.period_end is None and reported:
                ref.period_end = _day(reported)
        listed = self.source.earnings_releases(self.company, since)
        self.releases = sorted([ref for ref in listed if ref.confirmed] + results_releases(
            listed, self.reports, self.facts, self.text), key=lambda ref: ref.filed)

    def by_accession(self, accession: str):
        return next((ref for ref in [*self.reports, *self.releases] if ref.accession == accession), None)

    def near(self, end, role: str):
        refs = self.releases if role == "earnings_release" else self.reports
        return next((ref for ref in reversed(refs) if ref.period_end and abs(ref.period_end - end) <= timedelta(days=10)), None)

    def facts(self, ref):
        if ref.role != "periodic_report":
            return None
        if ref.accession not in self._facts:
            try:
                xbrl = self.source.tagged_facts(ref)
                self._facts[ref.accession] = None if xbrl is None else facts_of(xbrl.facts.to_dataframe())
            except Exception as error:  # noqa: BLE001
                self.log(f"  {ref.accession}: XBRL not read ({type(error).__name__})")
                self._facts[ref.accession] = None
        return self._facts[ref.accession]

    def document(self, ref):
        if ref.accession not in self._documents:
            try:
                if ref.role == "earnings_release":
                    document = None
                    for index, exhibit in enumerate(ref.exhibits[:6]):
                        raw = self.source.exhibit_html(exhibit)
                        if raw:
                            part = parse_document(raw, exhibit.url, prefix=f"{chr(65 + index)}_")
                            document = part if document is None else (document.extend(part) or document)
                else:
                    document = parse_document(self.source.report_html(ref), ref.source_url)
            except Exception as error:  # noqa: BLE001
                self.log(f"  {ref.accession}: not read ({type(error).__name__})")
                document = None
            self._documents[ref.accession] = document
        return self._documents[ref.accession]

    def text(self, ref) -> str:
        """A release's exhibits as plain text, for telling a foreign filer's results 6-K apart (owner/foreign.py)."""
        try:
            return " ".join(plain_text(raw) for exhibit in ref.exhibits[:6] if (raw := self.source.exhibit_html(exhibit)))
        except Exception as error:  # noqa: BLE001
            self.log(f"  {ref.accession}: not read ({type(error).__name__})")
            return ""

    def evidence(self, row: dict, ref) -> Evidence:
        return Evidence(row, ref, lambda: self.facts(ref), lambda: self.document(ref))


def _quarter_rows(rows: list[dict], key: str) -> list[dict]:
    own = [r for r in rows if f"{r['group_key']}.{r['kpi_key']}" == key and r.get("owner_decision") == "confirmed"
           and r.get("validation_status") == "verified"]
    return sorted(own, key=lambda r: r["period_end"], reverse=True)


def derive_company(symbol: str, spec: dict, rows: list[dict], filings: Filings, existing=None, log=print):
    refresh = OwnerRefresh(symbol, spec, rows, {}, facts=lambda ref: None, document=lambda ref: None)
    quarterly, annual = refresh.current_lines(), refresh.lines(annual=True)
    results: dict[str, LineResult] = {}
    evidence_of: dict[str, dict[str, list[Evidence]]] = {}
    for key, line in {**quarterly, **annual}.items():
        mine = _quarter_rows(rows, key)
        quarters = [r for r in mine if r["fiscal_period"] in _QUARTERS][:CHECKED]
        years = [r for r in mine if r["fiscal_period"] == "FY"][:2]
        releases = [filings.evidence(r, ref) for r in quarters if (ref := filings.near(_day(r["period_end"]), "earnings_release"))]
        reports = [filings.evidence(r, ref) for r in quarters if r["fiscal_period"] != "Q4" and r.get("method") != "derived"
                   and (ref := filings.near(_day(r["period_end"]), "periodic_report"))]
        if key in annual:
            reports = [filings.evidence(r, ref) for r in years if (ref := filings.near(_day(r["period_end"]), "periodic_report"))]
            releases = []
        prior = existing.lines.get(key) if existing else None
        result = derive_line(line, releases, reports, prior, balance=line.template.get("group_kind") == "metric"
                             and not any(r["fiscal_period"] == "FY" for r in mine))
        results[key] = result
        evidence_of[key] = {"release": releases, "report": reports}
    groups: dict[str, GroupMapping] = {}
    for group in sorted({key.split(".")[0] for key in results}):
        members = [key for key in results if key.startswith(f"{group}.")]
        totals = {}
        for origin in ("release", "report"):
            pairs = []
            for key in members[:1]:
                for item in evidence_of[key][origin]:
                    total = (item.row.get("locator") or {}).get("total")
                    if total is not None:
                        pairs.append((item, float(total)))
            parts = {k: v[origin] for k, v in evidence_of.items()}
            prior = existing.groups.get(group) if existing else None
            totals[origin] = derive_total(group, parts, pairs, {}, origin, prior)
        fy_rows = any(r["fiscal_period"] == "FY" and r["group_key"] == group for r in rows)
        groups[group] = GroupMapping(release_total=totals["release"], report_total=totals["report"], fy_rows=fy_rows)
    return results, groups


def replay(symbol: str, spec: dict, rows: list[dict], filings: Filings) -> dict:
    """The newest quarter, as the refresh would write it from its filings, against what the owner stored."""
    quarters = sorted({(r["period_end"], r["fiscal_year"], r["fiscal_period"]) for r in rows if r["fiscal_period"] in _QUARTERS
                       and r.get("owner_decision")})
    if not quarters:
        return {"period": None}
    end, year, period = quarters[-1]
    before = [r for r in rows if not (r["fiscal_year"] == year and r["fiscal_period"] == period)]
    stored = {(r["group_key"], r["kpi_key"]): float(r["value"]) for r in rows if r["fiscal_year"] == year
              and r["fiscal_period"] == period and r.get("owner_decision")}
    refs = [ref for ref in (filings.near(_day(end), "earnings_release"), filings.near(_day(end), "periodic_report")) if ref]
    plan = OwnerRefresh(symbol, spec, before, {}, facts=filings.facts, document=filings.document).plan(
        [ref for ref in refs if ref.role == "periodic_report"], [ref for ref in refs if ref.role == "earnings_release"])
    written = {(r["group_key"], r["kpi_key"]): r for r in plan.rows if (r["fiscal_year"], r["fiscal_period"]) == (year, period)}
    return {
        "period": f"{period} {year}", "stored": len(stored),
        "same": sorted(f"{g}.{k}" for (g, k), r in written.items() if (g, k) in stored and not differs(r["value"], stored[(g, k)])),
        "different": sorted(f"{g}.{k}: {r['value']:,.0f} vs {stored[(g, k)]:,.0f}" for (g, k), r in written.items()
                            if (g, k) in stored and differs(r["value"], stored[(g, k)])),
        "extra": sorted(f"{g}.{k}" for (g, k) in written if (g, k) not in stored),
        "not_written": sorted(f"{g}.{k}" for (g, k) in stored if (g, k) not in written),
        "held": sorted(f"{g}.{k}" for (g, k), r in written.items() if r["validation_status"] != "verified"),
        "hand": sorted(f"{h.group_key}.{h.kpi_key or '*'} ({h.kind})" for h in plan.hand),
    }


def sql_update(symbol: str, sources_json: str) -> str:
    tag = "owner_sources"
    assert f"${tag}$" not in sources_json
    return (f"update public.business_kpi_specs set sources = ${tag}${sources_json}${tag}$::jsonb\n"
            f" where symbol = '{symbol}' and status = 'active' and model = 'owner';\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True, help="read-only export: {specs: [...], values: [...]}")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--out", default="owner_sources")
    parser.add_argument("--sql", default="owner_sources/backfill.sql")
    parser.add_argument("--report", default=None, help="default: <out>/backfill_report.json")
    args = parser.parse_args(argv)
    state = json.loads(Path(args.state).read_text())
    wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = Path(args.report) if args.report else out / "backfill_report.json"
    statements, summary = [], []
    for spec in sorted(state["specs"], key=lambda s: s["symbol"]):
        symbol = spec["symbol"]
        if spec.get("model") != "owner" or (wanted and symbol not in wanted):
            continue
        rows = [r for r in state["values"] if r["symbol"] == symbol]
        print(f"{symbol}: {len(rows)} owner rows", flush=True)
        path = out / f"{symbol}.json"
        existing, problem = parse_sources(json.loads(path.read_text())) if path.exists() else (None, None)
        newest = max((_day(r["period_end"]) for r in rows), default=None)
        if newest is None:
            continue
        try:
            filings = Filings(symbol, newest - timedelta(days=500), log=print)
            results, groups = derive_company(symbol, spec, rows, filings, existing)
        except Exception as error:  # noqa: BLE001 - one company's failure is reported, the rest go on
            summary.append({"symbol": symbol, "error": f"{type(error).__name__}: {error}"[:300]})
            print(f"  failed: {error}", flush=True)
            continue
        sources = sources_from(results, groups, "derived 2026-11-25 by kpi_extractor.owner.backfill from the owner's "
                                                "stored figures and their filings" if existing is None else existing.derived_from)
        unmapped = {key: r.problem for key, r in results.items() if r.mapping is None}
        candidate = {**spec, "sources": json.loads(sources.model_dump_json(exclude_none=True))}
        check = replay(symbol, candidate, rows, filings)
        # A source the newest quarter disproves is no source: the line goes to the owner's list (Chevron's release gives
        # international production rounded; ISRG's report tables matched a footnote's figure in earlier quarters).
        for entry in check.get("different", []):
            key = entry.split(":", 1)[0]
            sources.lines.pop(key, None)
            unmapped[key] = f"the replay of {check['period']} gives{entry.split(':', 1)[1]} (stored): source dropped"
        text = sources.model_dump_json(indent=2, exclude_none=True)
        path.write_text(text + "\n")
        statements.append(f"-- {symbol}: {len(results) - len(unmapped)} of {len(results)} lines mapped; replay "
                          f"{check.get('period')}: {len(check.get('same', []))} same, {len(check.get('different', []))} different\n"
                          + sql_update(symbol, json.dumps(json.loads(text), separators=(",", ":"))))
        summary.append({"symbol": symbol, "lines": len(results), "mapped": len(results) - len(unmapped),
                        "unmapped": unmapped, "replay": check,
                        "notes": {key: r.notes for key, r in results.items() if r.notes}})
        print(f"  {len(results) - len(unmapped)}/{len(results)} mapped; replay {check.get('period')}: "
              f"{len(check.get('same', []))} same, {check.get('different')}", flush=True)
        report.write_text(json.dumps(summary, indent=2) + "\n")
    Path(args.sql).write_text("-- Owner line sources (business_kpi_specs.sources). Run only with the owner's Allow.\n"
                              "begin;\n" + "\n".join(statements) + "commit;\n")
    report.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {len(statements)} updates to {args.sql}; report in {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
