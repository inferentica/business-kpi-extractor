"""Reads one company's statements into files, and scores a set of such reads. Stores nothing anywhere else: the pilot
runs on GitHub-hosted runners and hands its files back as the run's artifact.

    statement-extractor read --symbol AAPL --since 2009-01-01 --out out/AAPL
    statement-extractor report out/*
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from .base import company_profile, configure_identity, learn_year_offset, load_submission, periodic_reports
from .choose import choose, mixed_vintage_q4s
from .golden import report_text, score
from .xbrl_reader import read_filing

# AI calls this package makes, by design: none. Reported in every summary so a run's cost is on record.
AI_CALLS = 0


def expected_own(form: str, fiscal_period: str) -> list[tuple[str, str]]:
    """The (statement, duration) a filing's own period should give."""
    if form.upper().startswith(("10-K", "20-F", "40-F")):
        return [("income", "year"), ("balance", "instant"), ("cash_flow", "year")]
    flows = "quarter" if fiscal_period == "Q1" else "ytd"
    expected = [("income", "quarter"), ("balance", "instant"), ("cash_flow", flows)]
    if fiscal_period != "Q1":
        expected.append(("income", "ytd"))
    return expected


def read_company(symbol: str, since: date, out: Path, log=print) -> dict:
    configure_identity()
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    profile, company = company_profile(symbol)
    reports = sorted(periodic_reports(company, since), key=lambda ref: ref.filed, reverse=True)
    readings, filings, offset = [], [], None
    for ref in reports:
        record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False}
        try:
            load_submission(ref.filing)
            xbrl = ref.filing.xbrl()
        except Exception as error:  # noqa: BLE001 - one unreadable filing never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            filings.append(record)
            continue
        if xbrl is None:
            record["note"] = "no XBRL"
            filings.append(record)
            continue
        entity = xbrl.entity_info or {}
        if offset is None:
            # The company's own fiscal-year naming, from its newest filing that states it (NVIDIA's FY2027 ends in
            # January 2027).
            end, year = entity.get("document_period_end_date"), entity.get("fiscal_year")
            if end and year:
                offset = learn_year_offset([(date.fromisoformat(str(end)[:10]), str(year), False)], profile.fiscal_year_end)
        found, notes = read_filing(xbrl, accession=ref.accession, form=ref.form, filed=ref.filed,
                                   fiscal_year_end=profile.fiscal_year_end, year_offset=offset or 0)
        own = [reading for reading in found if reading.own]
        record.update({"xbrl": True, "period_end": str(entity.get("document_period_end_date"))[:10],
                       **_own_period(own, ref.form),
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       "columns": len(found), "notes": notes})
        filings.append(record)
        readings.extend(found)
        log(f"{symbol}: {ref.form} {ref.filed} {len(found)} columns, own {[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
        del xbrl
        ref.filing = None
        gc.collect()
    chosen = choose(readings)
    summary = summarise(symbol, profile, since, filings, readings, chosen, time.time() - started)
    with (out / "readings.jsonl").open("w") as handle:
        for reading in readings:
            handle.write(json.dumps(reading.to_json(), default=str) + "\n")
    (out / "chosen.json").write_text(json.dumps(chosen, default=str))
    (out / "filings.json").write_text(json.dumps(filings, default=str, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, default=str, indent=1))
    return summary


def _own_period(own: list, form: str) -> dict:
    """The filing's own fiscal year and period: its balance sheet's, else any of its own columns' (a year for an annual
    report)."""
    ordered = sorted(own, key=lambda reading: reading.statement != "balance")
    if not ordered:
        return {"fiscal_year": None, "fiscal_period": None}
    annual = form.upper().startswith(("10-K", "20-F", "40-F"))
    return {"fiscal_year": ordered[0].fiscal_year, "fiscal_period": "FY" if annual else ordered[0].fiscal_period}


def summarise(symbol, profile, since, filings, readings, chosen, seconds) -> dict:
    by_status: dict[str, int] = {}
    for item in chosen:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    kept = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"])
            for item in chosen if item["status"] == "kept"}
    expected = found = 0
    missing = []
    for filing in filings:
        if not filing.get("xbrl") or not filing.get("fiscal_period"):
            continue
        for statement, duration in expected_own(filing["form"], filing["fiscal_period"]):
            expected += 1
            if (statement, filing["fiscal_year"], filing["fiscal_period"], duration) in kept:
                found += 1
            else:
                missing.append(f"{filing['form']} {filing['filed']} {statement}/{duration}")
    own_keys = {r.key for r in readings if r.own}
    return {
        "symbol": symbol, "name": profile.name, "cik": profile.cik, "fiscal_year_end": profile.fiscal_year_end,
        "foreign": profile.foreign, "since": since.isoformat(),
        "filings": len(filings), "xbrl_filings": sum(1 for f in filings if f.get("xbrl")),
        "no_xbrl_filings": [f"{f['form']} {f['filed']}" for f in filings if not f.get("xbrl")],
        "periods": len(chosen), "by_status": by_status,
        "own_expected": expected, "own_kept": found, "own_missing": missing[:40],
        "comparative_only_periods": sum(1 for item in chosen if item["status"] == "kept"
                                        and (item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]) not in own_keys),
        "restated_periods": sum(1 for item in chosen if (item.get("versus_own") or {}).get("changed")),
        "mixed_vintage_q4": mixed_vintage_q4s(chosen),
        "first_period": min((item["period_end"] for item in chosen if item["status"] == "kept"), default=None),
        "readings": len(readings), "rejected_readings": sum(1 for r in readings if r.status == "rejected"),
        "unproven_readings": sum(1 for r in readings if r.status == "unproven"),
        "ai_calls": AI_CALLS, "cost_cny": 0.0, "seconds": round(seconds),
    }


def report(folders: list[Path]) -> str:
    rows = []
    for folder in folders:
        path = folder / "summary.json"
        if path.exists():
            rows.append(json.loads(path.read_text()))
    header = ("| Company | Filings (XBRL) | First period | Periods kept | Rejected | Unproven | Own coverage | "
              "From comparatives only | Restated | Mixed-vintage Q4 | AI calls | ¥ |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [header]
    total = {"expected": 0, "kept": 0}
    for row in sorted(rows, key=lambda item: item["symbol"]):
        status = row["by_status"]
        coverage = row["own_kept"] / row["own_expected"] if row["own_expected"] else 0
        total["expected"] += row["own_expected"]
        total["kept"] += row["own_kept"]
        lines.append(f"| {row['symbol']} | {row['filings']} ({row['xbrl_filings']}) | {row['first_period']} | "
                     f"{status.get('kept', 0)} | {status.get('rejected', 0)} | {status.get('unproven', 0)} | "
                     f"{row['own_kept']}/{row['own_expected']} ({coverage:.1%}) | {row['comparative_only_periods']} | "
                     f"{row['restated_periods']} | {len(row['mixed_vintage_q4'])} | {row['ai_calls']} | {row['cost_cny']:.2f} |")
    if total["expected"]:
        lines.append(f"\nOwn-period coverage, all companies: {total['kept']}/{total['expected']} "
                     f"({total['kept'] / total['expected']:.1%}). AI calls: 0. Cost: ¥0.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="statement-extractor")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read", help="Read one company's statements into files")
    read.add_argument("--symbol", required=True)
    read.add_argument("--since", default="2009-01-01")
    read.add_argument("--out", required=True)
    scored = commands.add_parser("report", help="Score a set of reads")
    scored.add_argument("folders", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "read":
        summary = read_company(args.symbol.upper(), date.fromisoformat(args.since), Path(args.out))
        print(json.dumps({key: summary[key] for key in ("symbol", "periods", "by_status", "own_kept", "own_expected")}))
        return 0
    folders = [Path(folder) for folder in args.folders]
    scored = score(folders)
    text = report([Path(folder) for folder in args.folders]) + "\n\n" + report_text(scored)
    print(text)
    Path("golden_results.json").write_text(json.dumps(scored, default=str, indent=1))
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
