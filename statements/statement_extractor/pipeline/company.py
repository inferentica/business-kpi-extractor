"""Reads one company's statements into files. Stores nothing anywhere else: the pilot
runs on GitHub-hosted runners and hands its files back as the run's artifact.

    statement-extractor read --symbol AAPL --since 2009-01-01 --out out/AAPL
    statement-extractor report out/*
"""
from __future__ import annotations

import gc
import json
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from ..checks.across import cash_against_balances
from ..domain.basis import BARE_DOLLARS, BARE_PESOS, same_kind
from ..domain.calendar import ANNUAL_FORMS, year_end_for
from ..domain.model import REGULATOR_SOURCES
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period, summarise
from ..readers.xbrl.read import read_filing
from ..selection.choose import _reporting_currencies, choose
from ..selection.period_dates import align_dates
from ..selection.signs import sign_ties
from ..selection.summaries import choose_summaries, summary_coverage
from ..sources.edgar import company_profile, configure_identity, learn_year_offset, load_submission
from ..sources.listing import filing_for, list_reports
from ..sources.predecessors import predecessor_companies
from ..sources.retry import with_network_retries
from .prefetch import load_all
from .printed import read_printed
from .archives import read_archives
from .regulators import read_regulators
from .releases import read_releases


def read_company(symbol: str, since: date, out: Path, log=print) -> dict:
    configure_identity()
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    phases: dict[str, float] = {}  # seconds each part of the read took, kept in the summary (where a round's time goes)
    mark = time.time()
    profile, company = company_profile(symbol)
    predecessors = predecessor_companies(symbol)  # registrants its reporting passed from (Embraer's before 2006)
    reports = sorted(list_reports(company, since, predecessors), key=lambda ref: ref.filed, reverse=True)
    mark = _phase(phases, "listing", mark)
    readings, filings, offset, summaries, regulator_reads = [], [], None, [], []
    # Every report's period is read from its submission, so they are downloaded first, four at a time
    # (pipeline/prefetch.py); each is held on its filing until read, as it was when they came one by one.
    load_all([ref.filing for ref in reports], load_submission)
    mark = _phase(phases, "reports download", mark)
    annual_ends = [end for ref in reports if ref.form in ANNUAL_FORMS and (end := _period_end(ref)) is not None]
    year_ends = {ref.accession: year_end_for(_period_end(ref), annual_ends, profile.fiscal_year_end) for ref in reports}
    for ref in reports:
        fiscal_year_end = year_ends[ref.accession]
        record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False}
        try:
            xbrl = with_network_retries(lambda: (load_submission(ref.filing), ref.filing.xbrl())[1])
        except Exception as error:  # noqa: BLE001 - one unreadable filing never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            filings.append(record)
            continue
        if xbrl is None:
            found, notes, kinds = read_printed(ref, fiscal_year_end, offset or 0, summaries=summaries)
            printed_end = _printed_year_end(notes, ref) if ref.form in ANNUAL_FORMS and _period_end(ref) is None else None
            if printed_end and printed_end != fiscal_year_end:
                # No period on EDGAR and its years off the company's calendar: its own year end (Gildan's 40-Fs to
                # 2013 close in late September, before its move to calendar years).
                fiscal_year_end = year_ends[ref.accession] = printed_end
                found, notes, kinds = read_printed(ref, fiscal_year_end, offset or 0, summaries=summaries)
            own = [reading for reading in found if reading.own]
            record.update({"note": "no XBRL", "printed": kinds, "columns": len(found), "notes": notes,
                           "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                           **own_period(own, ref.form)})
            if not own and ref.form in ANNUAL_FORMS and (end := _period_end(ref)) is not None:
                # An annual report whose statements came elsewhere (Brookfield's FY2010 40-F holds its annual
                # information form; the statements came on 6-K): its year, so another filing's statements count.
                labelled = label_period(Period(end), fiscal_year_end, offset or 0)
                if labelled:
                    record.update({"fiscal_year": labelled[0], "fiscal_period": "FY"})
            filings.append(record)
            readings.extend(found)
            log(f"{symbol}: {ref.form} {ref.filed} printed ({','.join(kinds) or 'nothing readable'}) {len(found)} columns, "
                f"own {[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
            ref.filing = None
            gc.collect()
            continue
        entity = xbrl.entity_info or {}
        if offset is None:
            # The company's own fiscal-year naming, from its newest filing that states it (NVIDIA's FY2027 ends in
            # January 2027).
            end, year = entity.get("document_period_end_date"), entity.get("fiscal_year")
            if end and year:
                offset = learn_year_offset([(date.fromisoformat(str(end)[:10]), str(year), False)], profile.fiscal_year_end)
        found, notes = read_filing(xbrl, accession=ref.accession, form=ref.form, filed=ref.filed,
                                   fiscal_year_end=fiscal_year_end, year_offset=offset or 0)
        own = [reading for reading in found if reading.own]
        record.update({"xbrl": True, "period_end": str(entity.get("document_period_end_date"))[:10],
                       **own_period(own, ref.form),
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       "columns": len(found), "notes": notes})
        filings.append(record)
        readings.extend(found)
        log(f"{symbol}: {ref.form} {ref.filed} {len(found)} columns, own {[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
        del xbrl
        ref.filing = None
        gc.collect()
    mark = _phase(phases, "reports read", mark)
    if profile.foreign or any(ref.form in ("20-F", "40-F") for ref in reports):
        records, found = read_releases(symbol, company, since, profile.fiscal_year_end, offset or 0, log, phases, predecessors)
        filings.extend(records)
        readings.extend(found)
        archived_summaries: list[dict] = []
        mark = _phase(phases, "releases", mark)
        records, found = read_archives(symbol, profile.fiscal_year_end, offset or 0, log, archived_summaries)
        filings.extend(record for record in records if record["filed"] >= since.isoformat())  # the years on paper
        readings.extend(reading for reading in found if reading.filed >= since)
        summaries.extend(figure for figure in archived_summaries if figure["filed"] >= since.isoformat())
        mark = _phase(phases, "archives", mark)
        regulator_reads, found = read_regulators(symbol, since, profile.fiscal_year_end, offset or 0, readings, log)
        readings.extend(reading for reading in found if reading.filed >= since)
    mark = _phase(phases, "regulators", mark)
    printed_signs: dict[str, dict] = {}
    readings.extend(_printed_second_look(symbol, reports, filings, readings, year_ends, offset or 0, log,
                                         printed_signs))
    _release_bases(readings)
    _filing_currencies(readings)
    _bare_currencies(readings)
    readings = align_dates(readings)
    cash_against_balances(readings)  # cash flows printed without their cash balances (releases)
    mark = _phase(phases, "second looks", mark)
    chosen = choose(readings, printed_signs)
    mark = _phase(phases, "choose", mark)
    summary = summarise(symbol, profile, since, filings, readings, chosen, time.time() - started)
    summary_figures = choose_summaries(summaries, readings, chosen)
    summary["summary_figures"] = summary_coverage(summary_figures, chosen)
    summary["regulator_reads"] = regulator_reads
    summary["phases"] = phases
    with (out / "readings.jsonl").open("w") as handle:
        for reading in readings:
            handle.write(json.dumps(reading.to_json(), default=str) + "\n")
    (out / "chosen.json").write_text(json.dumps(chosen, default=str))
    (out / "filings.json").write_text(json.dumps(filings, default=str, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, default=str, indent=1))
    (out / "summary_figures.json").write_text(json.dumps({**summary_figures, "read": summaries}, default=str))
    return summary


def _printed_second_look(symbol, reports, filings, readings, year_ends: dict[str, str], year_offset: int, log,
                         printed_signs: dict) -> list:
    """The printed statements of XBRL filings that need them: one whose own statement failed its checks in XBRL (JPM's
    Q2 2010 balance sheet, which edgartools maps wrongly), and one whose served figure differs in sign from another
    presentation (the printed sign decides, from the statement or any table of the filing)."""
    failed = {reading.accession for reading in readings
              if reading.source == "xbrl" and reading.own and reading.status != "kept"}
    ties = sign_ties(readings)
    needed = failed | ties
    by_accession = {ref.accession: ref for ref in reports}
    records = {record["accession"]: record for record in filings}
    found_all = []
    for accession in sorted(needed):
        ref = by_accession.get(accession)
        if ref is None:
            continue
        try:
            ref.filing = filing_for(accession)
            found, notes, kinds = read_printed(ref, year_ends[accession], year_offset,
                                                printed_signs.setdefault(accession, {}) if accession in ties else None)
        except Exception as error:  # noqa: BLE001 - a second look that fails leaves the XBRL readings as they are
            records[accession]["second_look"] = f"unreadable ({type(error).__name__})"
            continue
        finally:
            ref.filing = None
        records[accession]["second_look"] = {"why": "failed in XBRL" if accession in failed else "sign",
                                             "printed": kinds, "columns": len(found), "notes": notes[:5]}
        log(f"{symbol}: {ref.form} {ref.filed} printed second look ({records[accession]['second_look']['why']}): "
            f"{len(found)} columns")
        found_all.extend(found)
    return found_all


def _phase(phases: dict, name: str, since: float) -> float:
    now = time.time()
    phases[name] = round(phases.get(name, 0.0) + now - since, 1)
    return now


def _period_end(ref) -> date | None:
    """The filing's period, as SEC's index states it. A period ending within a week of the filing date is no report
    period but the index's mistake (Helix's 10-Ks of 2014 and 2015 'end' on the day they were filed, which made
    February 21 its fiscal year end and every column of 2013-15 fall off the calendar): unknown."""
    try:  # SEC can answer with an error page for a filing's header; its period is then unknown, not fatal
        raw = getattr(ref.filing, "period_of_report", None)
        end = date.fromisoformat(str(raw)[:10]) if raw else None
    except Exception:  # noqa: BLE001
        return None
    return None if end is not None and (ref.filed - end).days < 7 else end


def _filing_currencies(readings) -> None:
    """A printed statement that states no currency is in the currency its own filing's XBRL gives the same statement
    (Brookfield's 40-Fs: their tags are in U.S. dollars, their printed pages name none)."""
    tagged = {(reading.accession, reading.statement): reading.currency for reading in readings
              if reading.source == "xbrl" and reading.currency}
    for reading in readings:
        if reading.source == "xbrl":
            continue
        own = tagged.get((reading.accession, reading.statement))
        if reading.currency is None or reading.currency in (BARE_DOLLARS, BARE_PESOS) and same_kind(reading.currency, own):
            reading.currency = own or reading.currency


def _bare_currencies(readings) -> None:
    """A statement printing only "$" or "pesos" is in the company's own reporting currency of that kind that year
    (Alcon's "($ millions)" in US dollars, Scotiabank's in Canadian dollars, GAP's "thousands of pesos" in Mexican
    pesos); a year whose annual reports give no single one of that kind leaves it without."""
    by_year = _reporting_currencies(readings)
    marked = [(reading, reading.currency) for reading in readings if reading.currency in (BARE_DOLLARS, BARE_PESOS)]
    for reading, marker in marked:
        kinds = [currency for currency in by_year.get(reading.fiscal_year, set()) if same_kind(marker, currency)]
        reading.currency = kinds[0] if len(kinds) == 1 else None
    # A statement's columns share its one "$": Brookfield's Q2 2001 release prints 2001 beside 2000, and 2000 is in
    # Canadian dollars (its 2002 annual report, the one presenting it), so 2001 is too, though 2003's report in US
    # dollars leaves the year itself undecided.
    decided: dict[tuple, set] = defaultdict(set)
    for reading, marker in marked:
        if reading.currency:
            decided[(reading.accession, reading.statement, marker)].add(reading.currency)
    for reading, marker in marked:
        found = decided.get((reading.accession, reading.statement, marker), set())
        if reading.currency is None and len(found) == 1:
            reading.currency = next(iter(found))


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
        same = [other for other in stated if other.period_end == own.period_end and abs((other.filed - own.filed).days) <= 183]
        if same and own.accession not in quarters:
            quarters[own.accession] = min(same, key=lambda other: abs((other.filed - own.filed).days)).basis
    for reading in readings:
        if reading.form == "6-K" and reading.basis is None and reading.accession in quarters:
            reading.basis = quarters[reading.accession]
        elif reading.form == "6-K" and reading.basis is None and (annual or reading.fiscal_year in home):
            # Else the nearest year's: TSMC's 1997-99 quarters, before its first 20-F we hold (FY2000, ROC GAAP).
            nearest = min(annual, key=lambda year: abs(int(year) - int(reading.fiscal_year))) if annual else None
            reading.basis = annual.get(reading.fiscal_year) or home.get(reading.fiscal_year) or annual[nearest]


_OFF_YEAR = re.compile(r"column duration_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2}) off the calendar")


def _printed_year_end(notes: list[str], ref) -> str | None:
    """The year end an annual report's own full-year columns print, when they fall off the company's calendar: the
    latest such column ending within a year before the report was filed."""
    ends = []
    for match in _OFF_YEAR.finditer(" ".join(notes)):
        start, end = date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
        if 350 <= (end - start).days + 1 <= 380 and 0 <= (ref.filed - end).days <= 366:
            ends.append(end)
    return f"{max(ends).month:02d}{max(ends).day:02d}" if ends else None
