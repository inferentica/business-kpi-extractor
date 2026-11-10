"""A foreign filer's years before EDGAR held its documents, read from archived copies of its own reports (sources/
archives.py): 20-Fs filed on paper, annual reports, quarterly statements. Each PDF is laid out as text by its words'
positions and read by the plain-text reader; the documents of one report (TSMC prints each quarter's balance sheet,
income statement and cash flows apart) are read as one filing."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..domain.basis import default_basis, default_currency, stated_basis, stated_currency
from ..domain.periods import Period, label_period
from ..quality.coverage import own_period
from ..readers.pdf.layout import pdf_text
from ..readers.printed.summary import read_summaries
from ..readers.printed.titled import split_titled
from ..readers.text.parse import parse_text
from ..readers.text.read import read_document as read_text
from ..sources.archives import archived, fetch


def read_archives(symbol: str, fiscal_year_end: str, year_offset: int, log=print, summaries: list | None = None):
    """(filing records, readings) of every archived report the company has; with `summaries`, the multi-year summary
    figures of its annual reports."""
    reports = defaultdict(list)
    for item in archived(symbol):
        reports[(item.form, item.period_end)].append(item)
    records, readings = [], []
    for (form, period_end), items in sorted(reports.items(), key=lambda entry: entry[0][1]):
        first = items[0]
        record = {"accession": first.accession, "form": form, "filed": first.filed.isoformat(), "xbrl": False,
                  "archive": [item.replay for item in items], "period_end": period_end.isoformat()}
        if form == "6-K":
            quarter = label_period(Period(period_end, period_end - timedelta(days=90)), fiscal_year_end, year_offset)
            record["release_quarter"] = list(quarter[:2]) if quarter else None
        found, notes, held, titled = [], [], set(), set()
        for item in items:
            try:
                text = pdf_text(fetch(item))
                document, document_notes = read_text(text, accession=first.accession, form=form, filed=first.filed,
                                                     document_end=period_end, fiscal_year_end=fiscal_year_end,
                                                     year_offset=year_offset)
            except Exception as error:  # noqa: BLE001 - one unreadable capture never stops the company
                notes.append(f"{item.url.rsplit('/', 1)[-1]}: unreadable ({type(error).__name__}: {str(error)[:80]})")
                continue
            printed_titles, document_notes = split_titled(document_notes)
            titled |= printed_titles
            basis, currency = stated_basis(text), stated_currency(text)
            if summaries is not None and form in ("20-F", "ARS"):
                for figure in read_summaries(parse_text(text), accession=first.accession, form=form, filed=first.filed,
                                             fiscal_year_end=fiscal_year_end, year_offset=year_offset):
                    summaries.append({**figure, "document": item.replay, "basis": basis or default_basis(form),
                                      "currency": currency or default_currency(form)})
            for reading in document:
                reading.source = "archive"
                reading.basis = reading.basis or basis or default_basis(form)
                reading.currency = reading.currency or currency or default_currency(form)
            found.extend(reading for reading in document if reading.key not in held)
            held |= {reading.key for reading in document}
            notes.extend(f"{item.url.rsplit('/', 1)[-1]}: {note}" for note in document_notes)
        own = [reading for reading in found if reading.own]
        record.update({"note": "archive", "printed": ["pdf"], "titled": sorted(titled), "columns": len(found), "notes": notes[:10],
                       "own": sorted({(reading.statement, reading.duration, reading.status) for reading in own}),
                       **own_period(own, form)})
        records.append(record)
        readings.extend(found)
        log(f"{symbol}: archived {form} {period_end} {len(found)} columns, own "
            f"{[f'{s}/{d}:{st}' for s, d, st in record['own']]}")
    return records, readings
