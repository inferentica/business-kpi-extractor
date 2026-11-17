"""The printed statements of XBRL filings that need them: one whose own statement failed its checks in XBRL (JPM's
Q2 2010 balance sheet, which edgartools maps wrongly), and one whose served figure differs in sign from another
presentation (the printed sign decides, from the statement or any table of the filing). Each is a read unit
(pipeline/units.py); which filings need one is decided over all the company's readings every read."""
from __future__ import annotations

from datetime import date

from ..selection.signs import sign_ties
from ..sources.listing import filing_for
from .printed import read_printed
from .units import Units, readings_of, unit_id


def printed_second_look(symbol, reports, filings, readings, year_ends: dict[str, str], naming, log,
                        printed_signs: dict, units: Units | None = None) -> list:
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
        year_offset = naming.offset(_period_end(records.get(accession), ref))
        context = {"fiscal_year_end": year_ends[accession], "offset": year_offset, "signs": accession in ties}
        unit = units.reuse(unit_id("second", accession), context) if units is not None else None
        if unit is not None:
            records[accession]["second_look"] = unit["extra"]["second_look"]
            if accession in ties:
                printed_signs[accession] = unit["extra"]["signs"]
            found_all.extend(readings_of(unit))
            continue
        try:
            ref.filing = filing_for(accession)
            found, notes, kinds = read_printed(ref, year_ends[accession], year_offset,
                                                printed_signs.setdefault(accession, {}) if accession in ties else None)
        except Exception as error:  # noqa: BLE001 - a second look that fails leaves the XBRL readings as they are
            records[accession]["second_look"] = f"unreadable ({type(error).__name__})"
            continue  # not kept: read again next time
        finally:
            ref.filing = None
        records[accession]["second_look"] = {"why": "failed in XBRL" if accession in failed else "sign",
                                             "printed": kinds, "columns": len(found), "notes": notes[:5]}
        log(f"{symbol}: {ref.form} {ref.filed} printed second look ({records[accession]['second_look']['why']}): "
            f"{len(found)} columns")
        if units is not None:
            unit = units.keep("second", accession, context, None, found, accession=accession,
                              filed=ref.filed.isoformat(), extra={"second_look": records[accession]["second_look"],
                                                                  "signs": printed_signs.get(accession, {})})
            records[accession]["second_look"] = unit["extra"]["second_look"]
            if accession in ties:
                printed_signs[accession] = unit["extra"]["signs"]
            found = readings_of(unit)
        found_all.extend(found)
    return found_all


def _period_end(record: dict | None, ref):
    """A report's own period, as EDGAR gives it, else its filing date: the era its year naming comes from."""
    end = (record or {}).get("period_end")
    try:
        return date.fromisoformat(end[:10]) if end else ref.filed
    except ValueError:
        return ref.filed
