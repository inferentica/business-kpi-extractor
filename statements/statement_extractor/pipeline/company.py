"""Reads one company's statements into files. Stores nothing anywhere else: the pilot
runs on GitHub-hosted runners and hands its files back as the run's artifact.

    statement-extractor read --symbol AAPL --since 2009-01-01 --out out/AAPL
    statement-extractor report out/*
"""
from __future__ import annotations

import gc
import json
import time
from datetime import date
from pathlib import Path

from ..checks.across import cash_against_balances
from ..domain.calendar import ANNUAL_FORMS, year_end_for
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period, summarise
from ..readers.xbrl.read import read_filing
from ..selection.choose import choose
from ..selection.period_dates import align_dates
from ..selection.signs import sign_ties
from ..sources.edgar import company_profile, configure_identity, learn_year_offset, load_submission
from ..sources.listing import filing_for, list_reports
from .printed import read_printed
from .releases import read_releases


def read_company(symbol: str, since: date, out: Path, log=print) -> dict:
    configure_identity()
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    profile, company = company_profile(symbol)
    reports = sorted(list_reports(company, since), key=lambda ref: ref.filed, reverse=True)
    readings, filings, offset = [], [], None
    annual_ends = [end for ref in reports if ref.form in ANNUAL_FORMS and (end := _period_end(ref)) is not None]
    year_ends = {ref.accession: year_end_for(_period_end(ref), annual_ends, profile.fiscal_year_end) for ref in reports}
    for ref in reports:
        fiscal_year_end = year_ends[ref.accession]
        record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False}
        try:
            load_submission(ref.filing)
            xbrl = ref.filing.xbrl()
        except Exception as error:  # noqa: BLE001 - one unreadable filing never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            filings.append(record)
            continue
        if xbrl is None:
            found, notes, kinds = read_printed(ref, fiscal_year_end, offset or 0)
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
    if profile.foreign or any(ref.form in ("20-F", "40-F") for ref in reports):
        records, found = read_releases(symbol, company, since, profile.fiscal_year_end, offset or 0, log)
        filings.extend(records)
        readings.extend(found)
    printed_signs: dict[str, dict] = {}
    readings.extend(_printed_second_look(symbol, reports, filings, readings, year_ends, offset or 0, log,
                                         printed_signs))
    _release_bases(readings)
    readings = align_dates(readings)
    cash_against_balances(readings)  # cash flows printed without their cash balances (releases)
    chosen = choose(readings, printed_signs)
    summary = summarise(symbol, profile, since, filings, readings, chosen, time.time() - started)
    with (out / "readings.jsonl").open("w") as handle:
        for reading in readings:
            handle.write(json.dumps(reading.to_json(), default=str) + "\n")
    (out / "chosen.json").write_text(json.dumps(chosen, default=str))
    (out / "filings.json").write_text(json.dumps(filings, default=str, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, default=str, indent=1))
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


def _period_end(ref) -> date | None:
    try:  # SEC can answer with an error page for a filing's header; its period is then unknown, not fatal
        raw = getattr(ref.filing, "period_of_report", None)
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _release_bases(readings) -> None:
    """A release that states no basis is on the basis of the annual report that closes its fiscal year (TSMC's 2010
    management reports are on ROC GAAP like its FY2010 20-F)."""
    annual = {reading.fiscal_year: reading.basis for reading in readings
              if reading.own and reading.form in ANNUAL_FORMS and reading.basis}
    for reading in readings:
        if reading.form == "6-K" and reading.basis is None:
            reading.basis = annual.get(reading.fiscal_year)
