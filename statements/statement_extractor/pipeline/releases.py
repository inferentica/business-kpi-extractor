"""A foreign filer's quarters, read from the results releases it furnishes on 6-K. A 20-F or 40-F gives only the year;
the quarters are in the releases (ASML's summary statements, TSMC's management reports and reviewed quarterly
statements). Each 6-K furnished in the weeks after a quarter closes is read as that quarter's release; one that prints
no statements (monthly sales, notices) gives nothing."""
from __future__ import annotations

import gc
from datetime import date, timedelta

from ..domain.calendar import quarter_end_before
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period
from ..sources.edgar import load_submission
from ..sources.listing import list_releases
from .printed import read_printed


def read_releases(symbol: str, company, since: date, fiscal_year_end: str, year_offset: int, log=print):
    """(filing records, readings) of every release since `since`."""
    records, readings = [], []
    refs = list_releases(company, since, lambda day: quarter_end_before(day, fiscal_year_end))
    log(f"{symbol}: {len(refs)} 6-Ks in the release window")
    for ref in refs:
        quarter = label_period(Period(ref.period_end, ref.period_end - timedelta(days=90)), fiscal_year_end, year_offset)
        record = {"accession": ref.accession, "form": ref.form, "filed": ref.filed.isoformat(), "xbrl": False,
                  "release_quarter": list(quarter[:2]) if quarter else None, "period_end": ref.period_end.isoformat()}
        try:
            load_submission(ref.filing)
            found, notes, kinds = read_printed(ref, fiscal_year_end, year_offset, document_end=ref.period_end)
        except Exception as error:  # noqa: BLE001 - one unreadable release never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            records.append(record)
            continue
        finally:
            ref.filing = None
            gc.collect()
        own = [reading for reading in found if reading.own]
        record.update({"note": "release", "printed": kinds, "columns": len(found), "notes": notes[:10],
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       **own_period(own, ref.form)})
        records.append(record)
        readings.extend(found)
        if found:
            log(f"{symbol}: 6-K {ref.filed} release {len(found)} columns, own "
                f"{[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
    return records, readings
