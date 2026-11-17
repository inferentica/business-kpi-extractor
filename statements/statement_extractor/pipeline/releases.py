"""A foreign filer's quarters, read from the results releases it furnishes on 6-K. A 20-F or 40-F gives only the year;
the quarters are in the releases (ASML's summary statements, TSMC's management reports and reviewed quarterly
statements). Each 6-K furnished in the weeks after a quarter closes is read as that quarter's release; one that prints
no statements (monthly sales, notices) gives nothing."""
from __future__ import annotations

import gc
import os
import time
from datetime import date, timedelta

from ..domain.calendar import quarter_end_before
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period
from ..sources.edgar import load_submission
from ..sources.retry import with_network_retries
from ..sources.listing import list_releases
from .empty_releases import names_a_statement, remember, remembered
from .prefetch import ahead
from .printed import read_printed
from .units import Units, readings_of, record_of, unit_id


def read_releases(symbol: str, company, since: date, fiscal_year_end: str, naming, log=print,
                  phases: dict | None = None, predecessors: list = (), units: Units | None = None):
    """(filing records, readings) of every release since `since`; with `units`, one read before in the same context
    (its quarter, the year end and year naming of its era) is taken as it was (pipeline/units.py). Each release is read
    on its quarter's era (domain/naming.py): Gildan's releases before 2015 close their years in October."""
    records, readings = [], []
    refs = list_releases(company, since, lambda day: quarter_end_before(day, fiscal_year_end), predecessors)
    folder = os.environ.get("STATEMENTS_ARCHIVE_CACHE")
    known, empty = remembered(folder), set()
    log(f"{symbol}: {len(refs)} 6-Ks in the release window, {len(known & {ref.accession for ref in refs})} remembered empty")
    stored = {}
    eras = {ref.accession: (naming.year_end(ref.period_end), naming.offset(ref.period_end)) for ref in refs}
    for ref in refs:
        context = {"fiscal_year_end": eras[ref.accession][0], "offset": eras[ref.accession][1],
                   "quarter_end": ref.period_end.isoformat()}
        # A unit first, remembered empty or not: what the runner's cache remembers differs from run to run.
        unit = units.reuse(unit_id("release", ref.accession), context) if units is not None else None
        stored[ref.accession] = (unit, context)
    # The next releases download while one is read (pipeline/prefetch.py); those remembered empty or read before are
    # not fetched.
    for ref in ahead(refs, lambda ref: None if ref.accession in known or (stored.get(ref.accession) or (None,))[0]
                     else load_submission(ref.filing)):
        unit, context = stored.get(ref.accession, (None, None))
        year_end, year_offset = eras[ref.accession]
        if unit is not None:
            records.append(record_of(unit))
            readings.extend(readings_of(unit))
            if unit["extra"].get("empty"):
                empty.add(ref.accession)
            ref.filing = None
            continue
        record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False,
                  "accepted": ref.accepted,
                  "release_quarter": _quarter(ref.period_end, year_end, year_offset), "period_end": ref.period_end.isoformat()}
        if ref.accession in known:  # names no statement (pipeline/empty_releases.py): not fetched again
            empty.add(ref.accession)
            records.append(_kept(units, ref, context, {
                **record, "note": "release", "printed": [], "titled": [], "columns": 0,
                "notes": ["names no statement (remembered)"], "own": [], **own_period([], ref.form)}, [], True))
            continue
        try:
            with_network_retries(lambda: load_submission(ref.filing))
            titled: set = set()
            began = time.time()
            found, notes, kinds = read_printed(ref, year_end, year_offset, document_end=ref.period_end, titled=titled)
            latest = own_quarter(found, ref.period_end)
            if latest is not None:
                ref.period_end, titled = latest, set()
                found, notes, kinds = read_printed(ref, year_end, year_offset, document_end=latest, titled=titled)
                record.update({"release_quarter": _quarter(latest, year_end, year_offset), "period_end": latest.isoformat()})
            if phases is not None:  # reading, apart from waiting for the download (pipeline/company.py's phases)
                phases["releases read"] = round(phases.get("releases read", 0.0) + time.time() - began, 1)
            if not found and not titled and not names_a_statement(ref.filing.attachments) and not any(
                    "unreadable" in note or "too large" in note for note in notes):
                empty.add(ref.accession)
        except Exception as error:  # noqa: BLE001 - one unreadable release never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            log(f"{symbol}: 6-K {ref.filed} {record['note']}")  # in the job's log: round 93 lost every release unseen
            records.append(_kept(units, ref, context, record, [], False, complete=False))
            continue
        finally:
            ref.filing = None
            gc.collect()
        own = [reading for reading in found if reading.own]
        record.update({"note": "release", "printed": kinds, "titled": sorted(titled), "columns": len(found), "notes": notes[:10],
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       **own_period(own, ref.form)})
        if units is not None:
            unit = units.keep("release", ref.accession, context, record, found, extra={"empty": ref.accession in empty},
                              accession=ref.accession, filed=ref.filed.isoformat())
            record, found = record_of(unit), readings_of(unit)
        records.append(record)
        readings.extend(found)
        if found:
            log(f"{symbol}: 6-K {ref.filed} release {len(found)} columns, own "
                f"{[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
    remember(folder, empty)
    return records, readings


def _kept(units: Units | None, ref, context: dict, record: dict, found: list, empty: bool, complete: bool = True) -> dict:
    if units is None or context is None:
        return record
    return record_of(units.keep("release", ref.accession, context, record, found, extra={"empty": empty},
                                accession=ref.accession, filed=ref.filed.isoformat(), complete=complete))


def _quarter(end: date, fiscal_year_end: str, year_offset: int) -> list | None:
    quarter = label_period(Period(end, end - timedelta(days=90)), fiscal_year_end, year_offset)
    return list(quarter[:2]) if quarter else None


def own_quarter(found: list, period_end: date) -> date | None:
    """The quarter a release filed after the next quarter closed is still its own: its latest interim period, when none
    of its statements ends on the quarter before its filing and that period ended within the 200 days before. Copel's
    Q1 2008 report, filed on 10 July 2008: its cash flow's "2008 | 2007" read as periods ending in June (round 87)."""
    latest = max((reading.period_end for reading in found), default=None)
    interim = any(reading.period_end == latest and reading.fiscal_period != "FY" for reading in found)
    if interim and not any(reading.own for reading in found) and period_end - timedelta(days=200) <= latest < period_end:
        return latest
    return None
