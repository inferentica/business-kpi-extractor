"""A filing's statements read from its printed documents: the report itself and an annual report exhibit (EX-13) for a
periodic report, the furnished exhibits (EX-99) for a results release, in HTML (about 2001 onwards) or plain text
(about 1993-2000). Each document's basis is the one it states; its EX-27 schedules check what it prints."""
from __future__ import annotations

from datetime import date

from ..checks.schedules import parse_schedule, schedule_check
from ..domain.basis import (BARE_DOLLARS, BARE_PESOS, default_basis, default_currency, same_kind, stated_basis,
                             stated_currency)
from ..domain.calendar import ANNUAL_FORMS
from ..readers.html.parse import parse_html
from ..readers.html.read import read_document
from ..readers.printed.mexico_coded import MARKER as MEXICO_MARKER, coded_readings
from ..readers.printed.sign_index import printed_figures
from ..readers.printed.summary import read_summaries
from ..readers.positioned.layout import is_positioned, positioned_text
from ..readers.printed.titled import headed_kinds, split_titled
from ..readers.slides.parse import is_slides, parse_slides
from ..readers.slides.read import read_document as read_slides
from ..readers.text.parse import parse_text
from ..readers.text.read import read_document as read_text
from ..sources.documents import PRINTED_DOCUMENTS, release_documents
from .company_names import _NAMES, _cover_text, _other_companies, _other_issuers, another_registrant  # noqa: F401
from .unstated_scale import with_proven_scale

_ASSET_BACKED = ("EX-33", "EX-34", "EX-35")

_MAX_BYTES = 20_000_000  # a furnished quarterly report runs to a few MB; anything far larger is no statement


def read_printed(ref, fiscal_year_end: str, year_offset: int, signs: dict | None = None,
                 document_end: date | None = None, summaries: list | None = None,
                 titled: set | None = None) -> tuple[list, list[str], list[str]]:
    """A filing without XBRL, read from its printed documents. A release's own period is its quarter (`document_end`).
    With `signs`, every figure its tables print is also collected there by label; with `summaries`, an annual report's
    multi-year summary figures (readers/printed/summary.py); with `titled`, the statements it prints of its own period
    (readers/printed/titled.py)."""
    found, notes, kinds = [], [], []
    release = ref.form == "6-K"
    if document_end is None and not release:
        raw_end = getattr(ref.filing, "period_of_report", None)
        try:
            document_end = date.fromisoformat(str(raw_end)[:10]) if raw_end else None
        except ValueError:
            document_end = None
        if document_end is not None and ref.form in ANNUAL_FORMS and not _year_end(document_end, fiscal_year_end):
            document_end = None  # no year end: EDGAR gives Alcon's 2007 20-F 19 March 2006; its statements say which year
    attachments = list(ref.filing.attachments)
    other = another_registrant(ref, attachments) if release else None
    cover = _cover_text(attachments) if release else None
    if other:
        # A subsidiary's report furnished beside the company's (Lloyds Bank plc's under Lloyds Banking Group): another
        # issuer's statements, never the company's.
        return [], [f"another registrant's report ({other})"], ["other registrant"]
    if any(str(item.document_type or "").upper().startswith(_ASSET_BACKED) for item in attachments):
        # An asset-backed issuer's report the company files for a trust (Scotiabank's covered bonds): Regulation AB's
        # servicing exhibits, no statements of the company's own.
        return [], ["asset-backed issuer report (Regulation AB exhibits)"], ["asset-backed"]
    # A 40-F is a cover: a Canadian filer's audited statements are its exhibits (EX-99.1 or EX-99.2 by year).
    wanted = PRINTED_DOCUMENTS + (("EX-",) if ref.form.upper().startswith("40-F") else ())  # Scotiabank 2010-15: EX-1-3
    chosen = set(map(id, release_documents(attachments))) if release else None
    schedules, filing_basis, filing_currency, unscaled = [], None, None, []
    for attachment in attachments:
        kind = str(attachment.document_type or "").upper()
        if kind.startswith("EX-27"):
            content = attachment.content
            schedule = parse_schedule(content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or ""))
            if schedule is not None:
                schedules.append(schedule)
            continue
        if (id(attachment) not in chosen) if release else not kind.startswith(wanted):
            continue
        extension = str(attachment.extension or "").lower()
        content = attachment.content
        content = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
        if len(content) > _MAX_BYTES:
            notes.append(f"{attachment.document}: too large to read")
            continue
        printed = "html" if extension in (".htm", ".html") or "<tr" in content[:200_000].lower() else "text"
        if printed == "html" and is_slides(content):
            printed = "slides"  # slide images with their words hidden beneath (ASML's releases from 2025)
        elif printed == "html" and is_positioned(content):
            printed, content = "positioned", positioned_text(content)  # pages of placed text, no tables (UBS since 2023)
        reader = {"html": read_document, "text": read_text, "positioned": read_text, "slides": read_slides}[printed]
        try:
            readings, document_notes = reader(content, accession=ref.accession, form=ref.form,
                                              filed=ref.filed, document_end=document_end,
                                              fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        except Exception as error:  # noqa: BLE001 - one unreadable document never stops the filing
            notes.append(f"{attachment.document}: unreadable ({type(error).__name__})")
            continue
        if printed == "html" and MEXICO_MARKER in content[:3_000_000].upper():
            # Mexico's coded template furnished in a 6-K (readers/printed/mexico_coded.py): read by its account codes.
            readings = readings + coded_readings(parse_html(content), accession=ref.accession, form=ref.form,
                                                 filed=ref.filed, document_end=document_end,
                                                 fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        others = _other_companies(readings, ref, cover)
        if others:
            readings = [reading for reading in readings if reading.titled not in others]
            document_notes.append(f"another company's statements left out ({'; '.join(sorted(others))[:120]})")
        issuers = _other_issuers(readings, ref, cover)
        if issuers:
            # Another issuer's release furnished in the company's 6-K (FEMSA's carry Coca-Cola FEMSA's): its statements.
            readings = [reading for reading in readings if reading.release_of not in issuers]
            document_notes.append(f"another issuer's release left out ({'; '.join(issuers)[:120]})")
        printed_titles, document_notes = split_titled(document_notes)
        if titled is not None:
            titled |= printed_titles | {(kind,) for kind in headed_kinds(content)}
        if signs is not None:
            parse = {"html": parse_html, "text": parse_text, "positioned": parse_text, "slides": parse_slides}[printed]
            for key, figures in printed_figures(parse(content)).items():
                signs.setdefault(key, []).extend(figures)
        basis, currency = stated_basis(content), stated_currency(content)
        filing_basis, filing_currency = filing_basis or basis, filing_currency or currency
        if summaries is not None and ref.form in ANNUAL_FORMS and printed != "slides":
            tables = (parse_html if printed == "html" else parse_text)(content)
            for figure in read_summaries(tables, accession=ref.accession, form=ref.form, filed=ref.filed,
                                         fiscal_year_end=fiscal_year_end, year_offset=year_offset):
                summaries.append({**figure, "document": attachment.document, "basis": basis or default_basis(ref.form),
                                  "currency": currency or default_currency(ref.form)})
        if any("no single scale stated" in note for note in document_notes) and printed != "slides":
            unscaled.append((reader, content, basis, currency, attachment.document, readings))
        for reading in readings:
            reading.basis = basis
            reading.currency = _decided(reading.currency, currency)
        share_currency(readings)
        # One presentation per period and basis: ASML's 6-Ks file an IFRS interim report before the US GAAP release.
        held = {(reading.key, reading.basis) for reading in found}
        found.extend(reading for reading in readings if (reading.key, reading.basis) not in held)
        notes.extend(f"{attachment.document}: {note}" for note in document_notes)
        kinds.append(printed)
    for reader, content, basis, currency, document, scaled in unscaled:
        # Statements that print no scale, read only at a scale the filing's own figures prove.
        readings, proved = with_proven_scale(reader, content, schedules, list(found), accession=ref.accession,
                                             form=ref.form, filed=ref.filed, document_end=document_end,
                                             fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        held = {(reading.key, reading.basis) for reading in found}
        others = _other_companies(readings, ref, cover)
        readings = [reading for reading in readings if reading.titled not in others]
        for reading in readings:
            reading.basis, reading.currency = basis, _decided(reading.currency, currency)
        share_currency(scaled + readings)  # Petrobras' real release prints its cash flow with no scale as well
        found.extend(reading for reading in readings if (reading.key, reading.basis) not in held)
        notes.extend(f"{document}: {note}" for note in split_titled(proved)[1])
    if document_end is None and ref.form in ANNUAL_FORMS:
        _own_year(found, ref.filed)
    for reading in found:  # the filing's own EX-27 schedules, an independent check on its printed figures
        reading.basis = reading.basis or filing_basis or default_basis(ref.form)
        # A U.S. company's "($ in millions)" is its dollars, as an unmarked statement of its 10-K is (default_currency).
        reading.currency = _decided(_decided(reading.currency, filing_currency), default_currency(ref.form))
        check = schedule_check(reading, schedules)
        if check is not None:
            reading.checks.append(check)
    if schedules:
        kinds.append("ex27")
    return found, notes, sorted(set(kinds))


def _own_year(found: list, filed: date) -> None:
    """An annual report that names no period of its own (Alcon's 20-Fs of 2004-09 on EDGAR): its own year is the latest
    year end it prints in its last year before filing."""
    ends = [reading.period_end for reading in found if reading.fiscal_period == "FY"
            and reading.period_end <= filed and (filed - reading.period_end).days <= 366]
    if ends:
        latest = max(ends)
        for reading in found:
            reading.own = reading.fiscal_period == "FY" and abs((reading.period_end - latest).days) <= 7


def _year_end(day: date, fiscal_year_end: str) -> bool:
    """Within ten days of the company's fiscal year end in some year (52/53-week years end near it)."""
    month, last = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    for year in (day.year - 1, day.year, day.year + 1):
        try:
            end = date(year, month, min(last, 28 if month == 2 else last))
        except ValueError:
            continue
        if abs((day - end).days) <= 10:
            return True
    return False


def share_currency(readings: list) -> None:
    """A statement naming no currency is in the one its document's other statements all name: Petrobras' real release
    heads its balance sheet and income "R$ million" and its cash flow nothing, beside a US dollar release of the same
    quarter filed the same day. Two of the group's statements must name it: one parent-only table naming US dollars
    in Scotiabank's Canadian dollar reports says nothing of the rest."""
    named = {reading.currency for reading in readings if reading.currency}
    kinds = {reading.statement for reading in readings if reading.currency and reading.scope == "consolidated"}
    if len(named) == 1 and len(kinds) >= 2:
        for reading in readings:
            reading.currency = reading.currency or next(iter(named))


def _decided(printed: str | None, stated: str | None) -> str | None:
    """A statement's currency: its own, else the one its document states; a bare "$" or "pesos" takes the stated
    currency of its kind (Embraer's releases state U.S. dollars, their statements print "$"), else stays marked."""
    if printed in (BARE_DOLLARS, BARE_PESOS):
        return stated if same_kind(printed, stated) else printed
    return printed or stated
