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

from ..checks.schedules import parse_schedule, schedule_check
from ..domain.basis import default_basis, stated_basis
from ..domain.calendar import ANNUAL_FORMS, year_end_for
from ..quality.coverage import own_period, summarise
from ..readers.html.parse import parse_html
from ..readers.html.read import read_document
from ..readers.printed.sign_index import printed_figures
from ..readers.text.parse import parse_text
from ..readers.text.read import read_document as read_text
from ..readers.xbrl.read import read_filing
from ..selection.choose import choose
from ..selection.period_dates import align_dates
from ..selection.signs import sign_ties
from ..sources.documents import PRINTED_DOCUMENTS
from ..sources.edgar import company_profile, configure_identity, learn_year_offset, load_submission
from ..sources.listing import filing_for, list_reports


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
            found, notes, kinds = _read_printed(ref, fiscal_year_end, offset or 0)
            own = [reading for reading in found if reading.own]
            record.update({"note": "no XBRL", "printed": kinds, "columns": len(found), "notes": notes,
                           "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                           **own_period(own, ref.form)})
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
    printed_signs: dict[str, dict] = {}
    readings.extend(_printed_second_look(symbol, reports, filings, readings, year_ends, offset or 0, log,
                                         printed_signs))
    readings = align_dates(readings)
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
            found, notes, kinds = _read_printed(ref, year_ends[accession], year_offset,
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
    raw = getattr(ref.filing, "period_of_report", None)
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _read_printed(ref, fiscal_year_end: str, year_offset: int, signs: dict | None = None) -> tuple[list, list[str], list[str]]:
    """A filing without XBRL, read from its printed documents: the report itself and an annual report exhibit that
    holds its statements (EX-13), in HTML (about 2001-2008) or plain text (about 1993-2000). With `signs`, every
    figure its tables print is also collected there by label."""
    found, notes, kinds = [], [], []
    raw_end = getattr(ref.filing, "period_of_report", None)
    try:
        document_end = date.fromisoformat(str(raw_end)[:10]) if raw_end else None
    except ValueError:
        document_end = None
    schedules, filing_basis = [], None
    for attachment in ref.filing.attachments:
        kind = str(attachment.document_type or "").upper()
        if kind.startswith("EX-27"):
            content = attachment.content
            schedule = parse_schedule(content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or ""))
            if schedule is not None:
                schedules.append(schedule)
            continue
        if not kind.startswith(PRINTED_DOCUMENTS):
            continue
        extension = str(attachment.extension or "").lower()
        content = attachment.content
        content = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
        printed = "html" if extension in (".htm", ".html") or "<tr" in content[:200_000].lower() else "text"
        reader = read_document if printed == "html" else read_text
        try:
            readings, document_notes = reader(content, accession=ref.accession, form=ref.form,
                                              filed=ref.filed, document_end=document_end,
                                              fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        except Exception as error:  # noqa: BLE001 - one unreadable document never stops the filing
            notes.append(f"{attachment.document}: unreadable ({type(error).__name__})")
            continue
        if signs is not None:
            parse = parse_html if printed == "html" else parse_text
            for key, figures in printed_figures(parse(content)).items():
                signs.setdefault(key, []).extend(figures)
        basis = stated_basis(content)
        filing_basis = filing_basis or basis
        for reading in readings:
            reading.basis = basis
        held = {reading.key for reading in found}
        found.extend(reading for reading in readings if reading.key not in held)
        notes.extend(f"{attachment.document}: {note}" for note in document_notes)
        kinds.append(printed)
    for reading in found:  # the filing's own EX-27 schedules, an independent check on its printed figures
        reading.basis = reading.basis or filing_basis or default_basis(ref.form)
        check = schedule_check(reading, schedules)
        if check is not None:
            reading.checks.append(check)
    if schedules:
        kinds.append("ex27")
    return found, notes, sorted(set(kinds))
