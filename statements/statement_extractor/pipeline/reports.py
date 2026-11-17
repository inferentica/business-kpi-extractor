"""A company's EDGAR reports read one by one: XBRL where the filing carries it, its printed statements where it does
not. Each report is a read unit (pipeline/units.py): one read before in the same context is taken as it was, any
other is read from EDGAR."""
from __future__ import annotations

import gc
import re
from datetime import date

from ..checks.arelle import Checks, enabled as arelle_cache
from ..domain.calendar import ANNUAL_FORMS, year_end_for
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period
from ..readers.xbrl.read import read_filing
from ..readers.xbrl.taxonomy import tag_taxonomies, taxonomy_versions
from ..domain.naming import Naming
from ..sources.edgar import load_submission, period_of_report
from ..sources.retry import with_network_retries
from .prefetch import load_all
from .printed import read_printed
from .units import Units, readings_of, record_of, unit_id


def read_reports(symbol: str, reports: list, profile, units: Units, summaries: list, log=print, mark=None):
    """(readings, filing records, the year naming (domain/naming.py), each report's fiscal year end) of the reports,
    newest first."""
    # Every report's period comes from its submission, so those not read before are downloaded first, four at a time
    # (pipeline/prefetch.py); each is held on its filing until read, as it was when they came one by one.
    period_ends = {ref.accession: units.stored_extra(unit_id("report", ref.accession), "period_end") for ref in reports}
    load_all([ref.filing for ref in reports if period_ends[ref.accession] is None], load_submission)
    for ref in reports:
        if period_ends[ref.accession] is None:
            end = _period_end(ref)
            period_ends[ref.accession] = end.isoformat() if end else ""
    ends = {accession: date.fromisoformat(end) if end else None for accession, end in period_ends.items()}
    if mark:
        mark("reports download")
    annual_ends = [end for ref in reports if ref.form in ANNUAL_FORMS and (end := ends[ref.accession]) is not None]
    year_ends = {ref.accession: year_end_for(ends[ref.accession], annual_ends, profile.fiscal_year_end) for ref in reports}
    arelle = Checks(cache) if (cache := arelle_cache()) else None
    # The company's year naming, era by era (domain/naming.py): from the fiscal years its XBRL filings state, those
    # read before first, then each as it is read.
    stated = [(ref, units.stored_extra(unit_id("report", ref.accession), "entity")) for ref in reports]
    naming = Naming(annual_ends, profile.fiscal_year_end,
                    [_sample(entity, ref.form) for ref, entity in stated if entity])
    slots = []
    for ref in reports:
        id = unit_id("report", ref.accession)
        offset = naming.offset(ends[ref.accession] or ref.filed)
        unit = units.reuse(id, _context(year_ends[ref.accession], offset, ref, arelle))
        if unit is not None:
            year_ends[ref.accession] = unit["extra"].get("fiscal_year_end", year_ends[ref.accession])
            slots.append(unit)
            ref.filing = None
            continue
        slot = _read(symbol, ref, year_ends, ends[ref.accession], offset, profile, arelle, summaries, log)
        if slot["facts"].get("entity"):
            naming.add(_sample(slot["facts"]["entity"], ref.form))
        slots.append(slot)
    if mark:
        mark("reports read")
    if arelle:
        arelle.finish()
        if mark:
            mark("arelle")  # the wait for Arelle after the reader's last filing
    readings, filings = [], []
    summaries.clear()
    for slot in slots:
        unit = _keep(units, slot) if "ref" in slot else slot
        readings.extend(readings_of(unit))
        filings.append(record_of(unit))
        summaries.extend(unit["extra"].get("summaries", []))
    # Printed annual reports EDGAR gives no period for close their eras too (Gildan's 40-Fs to 2013).
    naming = naming.with_ends(reading.period_end for reading in readings
                              if reading.own and reading.form in ANNUAL_FORMS and reading.duration == "year")
    return readings, filings, naming, year_ends


def _sample(entity: list, form: str) -> tuple[date, str, bool]:
    """(period end, fiscal year stated, annual) from a filing's DocumentPeriodEndDate and DocumentFiscalYearFocus."""
    return date.fromisoformat(entity[0]), str(entity[1]), form in ANNUAL_FORMS


def _context(fiscal_year_end: str, offset: int | None, ref, arelle) -> dict:
    return {"fiscal_year_end": fiscal_year_end, "offset": offset, "form": ref.form, "arelle": bool(arelle)}


def _read(symbol, ref, year_ends: dict, period_end, offset: int, profile, arelle, summaries: list, log) -> dict:
    """One report read from EDGAR: its pending unit."""
    fiscal_year_end = year_ends[ref.accession]
    context = _context(fiscal_year_end, offset, ref, arelle)
    record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False,
              "accepted": ref.accepted}
    pending = {"ref": ref, "record": record, "found": [], "context": context, "facts": {
        "period_end": period_end.isoformat() if period_end else ""}, "extra": {}, "summaries_from": len(summaries)}
    try:
        xbrl = with_network_retries(lambda: (load_submission(ref.filing), ref.filing.xbrl())[1])
    except Exception as error:  # noqa: BLE001 - one unreadable filing never stops the company
        record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
        pending["complete"] = False
        if ref.form == "6-K":
            xbrl = None  # EDGAR's flag without an instance (2016-17); the release reader reads its pages
        else:
            return _close(pending, summaries)
    if xbrl is None and ref.form == "6-K":
        record.setdefault("note", "no XBRL")
        ref.filing = None
        return _close(pending, summaries)
    if xbrl is None:
        found, notes, kinds = read_printed(ref, fiscal_year_end, offset, summaries=summaries)
        printed_end = _printed_year_end(notes, ref) if ref.form in ANNUAL_FORMS and period_end is None else None
        if printed_end and printed_end != fiscal_year_end:
            # No period on EDGAR and its years off the company's calendar: its own year end (Gildan's 40-Fs to
            # 2013 close in late September, before its move to calendar years).
            fiscal_year_end = year_ends[ref.accession] = printed_end
            found, notes, kinds = read_printed(ref, fiscal_year_end, offset, summaries=summaries)
        own = [reading for reading in found if reading.own]
        record.update({"note": "no XBRL", "printed": kinds, "columns": len(found), "notes": notes,
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       **own_period(own, ref.form)})
        if not own and ref.form in ANNUAL_FORMS and period_end is not None:
            # An annual report whose statements came elsewhere (Brookfield's FY2010 40-F holds its annual
            # information form; the statements came on 6-K): its year, so another filing's statements count.
            labelled = label_period(Period(period_end), fiscal_year_end, offset)
            if labelled:
                record.update({"fiscal_year": labelled[0], "fiscal_period": "FY"})
        pending.update(found=found, extra={"fiscal_year_end": fiscal_year_end})
        log(f"{symbol}: {ref.form} {ref.filed} printed ({','.join(kinds) or 'nothing readable'}) {len(found)} columns, "
            f"own {[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
        ref.filing = None
        gc.collect()
        return _close(pending, summaries)
    entity = xbrl.entity_info or {}
    end, year = entity.get("document_period_end_date"), entity.get("fiscal_year")
    if end and year:
        pending["facts"]["entity"] = [str(end)[:10], str(year)]
    found, notes = read_filing(xbrl, accession=ref.accession, form=ref.form, filed=ref.filed,
                               fiscal_year_end=fiscal_year_end, year_offset=offset)
    own = [reading for reading in found if reading.own]
    record.update({"xbrl": True, "period_end": str(entity.get("document_period_end_date"))[:10],
                   **own_period(own, ref.form),
                   "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                   "columns": len(found), "notes": notes})
    tag_taxonomies(found, taxonomy_versions(ref.filing))  # us-gaap/2024: the years its schema imports
    if arelle and found:
        arelle.submit(ref.filing, found, record)  # Arelle's second reading of the filing, a check only
    pending["found"] = found
    log(f"{symbol}: {ref.form} {ref.filed} {len(found)} columns, own {[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
    del xbrl
    ref.filing = None
    gc.collect()
    return _close(pending, summaries)


def _close(pending: dict, summaries: list) -> dict:
    pending["extra"]["summaries"] = summaries[pending.pop("summaries_from"):]
    return pending


def _keep(units: Units, pending: dict) -> dict:
    ref, record = pending["ref"], pending["record"]
    checked = record.get("arelle") or {}
    # A filing unread, or that Arelle had no time for, is read again next time.
    complete = pending.get("complete", True) and checked.get("error") in (None, "no instance")
    return units.keep("report", ref.accession, pending["context"], record, pending["found"], extra=pending["extra"],
                      facts=pending["facts"], accession=ref.accession, filed=ref.filed.isoformat(), complete=complete)


def _period_end(ref) -> date | None:
    """The filing's period, as SEC's index states it. A period ending within a week of the filing date is no report
    period but the index's mistake (Helix's 10-Ks of 2014 and 2015 'end' on the day they were filed, which made
    February 21 its fiscal year end and every column of 2013-15 fall off the calendar): unknown."""
    raw = period_of_report(ref.filing)  # an SEC error page is read again, never taken for an unknown period
    try:
        end = date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None
    return None if end is not None and (ref.filed - end).days < 7 else end


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
