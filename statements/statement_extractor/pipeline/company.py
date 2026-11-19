"""Reads one company's statements into files. Stores nothing anywhere else: the pilot
runs on GitHub-hosted runners and hands its files back as the run's artifact.

    statement-extractor read --symbol AAPL --since 2009-01-01 --out out/AAPL
    statement-extractor report out/*
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from ..checks.across import cash_against_balances
from ..checks.arelle import arelle_summary, enabled as arelle_cache
from ..domain.calendar import ANNUAL_FORMS
from ..domain.line_fields import finish_lines
from ..domain.naming import name_readings
from ..domain.model import REGULATOR_SOURCES
from ..quality.coverage import summarise
from ..selection.choose import choose
from ..selection.fourth import derive_fourth_quarters
from ..selection.identity import apply_identity
from ..selection.period_dates import align_dates
from ..selection.splits import adjust_for_splits, split_ledger
from ..selection.summaries import choose_summaries, summary_coverage
from ..sources.edgar import company_profile, configure_identity
from ..sources.listing import list_reports, list_xbrl_sixks
from ..sources.predecessors import predecessor_companies
from .archives import read_archives
from .currencies import settle_currencies
from .regulators import read_regulators
from .releases import read_releases
from .reports import read_reports
from .second_look import printed_second_look
from .served import served_snapshot
from .standards import use_standard_labels
from .units import Units


def read_company(symbol: str, since: date, out: Path, log=print, previous: Path | None = None,
                 drop_newest: int = 0) -> dict:
    """With `previous` (an earlier read's folder, its units/), only what is new or read in another context is read
    (pipeline/units.py); `drop_newest` sets the newest filings' units aside, to prove the two reads agree."""
    configure_identity()
    use_standard_labels()  # the IFRS and US GAAP taxonomies' titles and key lines (pipeline/standards.py)
    out.mkdir(parents=True, exist_ok=True)
    units = Units(out, previous, drop_newest)
    started = time.time()
    phases: dict[str, float] = {}  # seconds each part of the read took, kept in the summary (where a round's time goes)
    mark = time.time()
    profile, company = company_profile(symbol)
    predecessors = predecessor_companies(symbol)  # registrants its reporting passed from (Embraer's before 2006)
    reports = list_reports(company, since, predecessors)
    if profile.foreign or any(ref.form in ("20-F", "40-F") for ref in reports):
        reports += list_xbrl_sixks(company, since, predecessors)  # read from their XBRL like a 10-Q
    reports = sorted(reports, key=lambda ref: ref.filed, reverse=True)
    mark = _phase(phases, "listing", mark)
    summaries, regulator_reads = [], []
    clock = {"mark": mark}

    def lap(name: str) -> None:
        clock["mark"] = _phase(phases, name, clock["mark"])

    readings, filings, naming, year_ends = read_reports(symbol, reports, profile, units, summaries, log, lap)
    offset = naming.current_offset()
    mark = clock["mark"]
    if profile.foreign or any(ref.form in ("20-F", "40-F") for ref in reports):
        records, found = read_releases(symbol, company, since, profile.fiscal_year_end, naming, log, phases,
                                       predecessors, units)
        filings.extend(records)
        readings.extend(found)
        archived_summaries: list[dict] = []
        mark = _phase(phases, "releases", mark)
        records, found = read_archives(symbol, profile.fiscal_year_end, offset, log, archived_summaries)
        filings.extend(record for record in records if record["filed"] >= since.isoformat())  # the years on paper
        readings.extend(reading for reading in found if reading.filed >= since)
        summaries.extend(figure for figure in archived_summaries if figure["filed"] >= since.isoformat())
        mark = _phase(phases, "archives", mark)
        regulator_reads, found = read_regulators(symbol, since, profile.fiscal_year_end, offset, readings, log,
                                                 units)
        readings.extend(reading for reading in found if reading.filed >= since)
    mark = _phase(phases, "regulators", mark)
    printed_signs: dict[str, dict] = {}
    readings.extend(printed_second_look(symbol, reports, filings, readings, year_ends, naming, log,
                                        printed_signs, units))
    # Every period named by the era its own end falls in (domain/naming.py).
    readings, left_out = name_readings(readings, naming)
    _release_bases(readings)
    settle_currencies(readings)  # pipeline/currencies.py
    finish_lines(readings)  # each line's unit, taxonomy, rounding and sign, its currency now known (domain/line_fields.py)
    readings = align_dates(readings)
    cash_against_balances(readings)  # cash flows printed without their cash balances (releases)
    mark = _phase(phases, "second looks", mark)
    chosen = choose(readings, printed_signs)
    keys = apply_identity(chosen, readings)  # one key per line across the company's eras (selection/identity.py)
    chosen += derive_fourth_quarters(chosen, readings, keys)  # fourth quarters of one vintage (selection/fourth.py)
    ledger = split_ledger(readings)
    adjust_for_splits(chosen, ledger)  # per-share figures on today's share basis (selection/splits.py)
    mark = _phase(phases, "choose", mark)
    summary = summarise(symbol, profile, since, filings, readings, chosen, time.time() - started)
    summary_figures = choose_summaries(summaries, readings, chosen)
    summary["summary_figures"] = summary_coverage(summary_figures, chosen)
    summary["regulator_reads"] = regulator_reads
    summary["phases"] = phases
    summary["naming"] = {"eras": naming.describe(), "left_out": left_out}
    summary["incremental"] = units.finish()
    if arelle_cache():
        summary["arelle"] = arelle_summary(readings, filings, arelle_cache())
    with (out / "readings.jsonl").open("w") as handle:
        for reading in readings:
            handle.write(json.dumps(reading.to_json(), default=str) + "\n")
    (out / "chosen.json").write_text(json.dumps(chosen, default=str))
    (out / "filings.json").write_text(json.dumps(filings, default=str, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, default=str, indent=1))
    (out / "summary_figures.json").write_text(json.dumps({**summary_figures, "read": summaries}, default=str))
    # What Quotiva serves, as statements_store takes it (pipeline/served.py); written there only from main.
    served = served_snapshot(symbol, summary.get("cik"), chosen, filings, ledger, summary)
    (out / "served.json").write_text(json.dumps(served, default=str))
    return summary


def _phase(phases: dict, name: str, since: float) -> float:
    now = time.time()
    phases[name] = round(phases.get(name, 0.0) + now - since, 1)
    return now


def _release_bases(readings) -> None:
    """A release that states no basis is on the basis of the annual report that closes its fiscal year (TSMC's 2010
    management reports are on ROC GAAP like its FY2010 20-F)."""
    annual = {reading.fiscal_year: reading.basis for reading in readings
              if reading.own and reading.form in ANNUAL_FORMS and reading.basis}
    # Else the home regulator's annual statements of that year: SQM's 2010, on IFRS at CMF, where the 20-F we hold
    # closest is 2009's on Chilean GAAP.
    home = {reading.fiscal_year: reading.basis for reading in readings
            if reading.own and reading.source in REGULATOR_SOURCES and reading.fiscal_period == "FY" and reading.basis}
    # First the quarter's own report, filed nearest the release: Sabesp's 2010 releases are on Brazilian GAAP like its
    # 2010 quarterly information, though its FY2010 20-F restated those quarters on IFRS (round 84).
    stated = [reading for reading in readings if reading.own and reading.form == "6-K" and reading.basis]
    quarters = {}  # each release by its own period: the basis its quarter's own report states
    for own in (reading for reading in readings if reading.own and reading.form == "6-K" and reading.basis is None):
        # Of its own scope: UBS's standalone statements of June 2023 are not on the US GAAP of the Credit Suisse
        # report it filed for the same quarter.
        same = [other for other in stated if other.period_end == own.period_end and other.scope == own.scope
                and abs((other.filed - own.filed).days) <= 183]
        if same and own.accession not in quarters:
            quarters[own.accession] = min(same, key=lambda other: abs((other.filed - own.filed).days)).basis
    for reading in readings:
        if reading.form == "6-K" and reading.basis is None and reading.accession in quarters:
            reading.basis = quarters[reading.accession]
        elif reading.form == "6-K" and reading.basis is None and (annual or reading.fiscal_year in home):
            # Else the nearest year's: TSMC's 1997-99 quarters, before its first 20-F we hold (FY2000, ROC GAAP).
            nearest = min(annual, key=lambda year: abs(int(year) - int(reading.fiscal_year))) if annual else None
            reading.basis = annual.get(reading.fiscal_year) or home.get(reading.fiscal_year) or annual[nearest]
