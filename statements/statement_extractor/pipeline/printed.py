"""A filing's statements read from its printed documents: the report itself and an annual report exhibit (EX-13) for a
periodic report, the furnished exhibits (EX-99) for a results release, in HTML (about 2001 onwards) or plain text
(about 1993-2000). Each document's basis is the one it states; its EX-27 schedules check what it prints."""
from __future__ import annotations

import re

from datetime import date

from ..checks.schedules import parse_schedule, schedule_check
from ..domain.basis import default_basis, default_currency, stated_basis, stated_currency
from ..domain.calendar import ANNUAL_FORMS
from ..readers.html.parse import parse_html
from ..readers.html.read import read_document
from ..readers.printed.sign_index import printed_figures
from ..readers.printed.summary import read_summaries
from ..readers.slides.parse import is_slides, parse_slides
from ..readers.slides.read import read_document as read_slides
from ..readers.text.parse import parse_text
from ..readers.text.read import read_document as read_text
from ..sources.documents import PRINTED_DOCUMENTS, release_documents
from .unstated_scale import with_proven_scale

_ASSET_BACKED = ("EX-33", "EX-34", "EX-35")
_REGISTRANT = re.compile(r"\(\s*(?:Translation\s+of\s+registrant|Exact\s+name\s+of\s+registrant)", re.I)
_BEFORE_NAME = re.compile(r".*(?:\d{3}-\d{4,6}|\b(?:19|20)\d{2}\b|\b1934\b|\bForm\s+6-K\b)\s*:?", re.I | re.S)
_SUFFIXES = {"THE", "PLC", "INC", "CORP", "CORPORATION", "LTD", "LIMITED", "CO", "COMPANY", "SA", "AG", "NV", "LLC", "ON"}
_NAMES: dict[str, list[str]] = {}


def _words(name: str) -> set[str]:
    return {word for word in re.sub(r"[^A-Z0-9]+", " ", name.upper()).split() if word not in _SUFFIXES}


def _company_names(filing) -> list[str]:
    """The company's name and its former names, as EDGAR records them."""
    cik = str(getattr(filing, "cik", "") or "")
    if cik not in _NAMES:
        names = [str(getattr(filing, "company", "") or "")]
        try:
            from edgar import Company
            names += [item["name"] for item in (Company(int(cik)).data.former_names or [])]
        except Exception:  # noqa: BLE001 - without former names, the current one is compared
            pass
        _NAMES[cik] = [name for name in names if name]
    return _NAMES[cik]


def another_registrant(ref, attachments) -> str | None:
    """The registrant a 6-K's cover names, when it is neither the company nor a name the company once had."""
    cover = next((item for item in attachments if str(item.document_type or "").upper().startswith("6-K")), None)
    if cover is None:
        return None
    content = cover.content
    text = re.sub(r"<[^>]+>|&#\d+;|&nbsp;", " ", content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or ""))
    text = re.sub(r"\s+", " ", text[:30_000])
    match = _REGISTRANT.search(text)
    if match is None:
        return None
    printed = _BEFORE_NAME.sub("", text[max(0, match.start() - 150):match.start()]).strip(" :.,")
    words = _words(printed)
    names = [_words(name) for name in _company_names(ref.filing)]
    if not words or not names or any(words <= name or name <= words for name in names if name):
        return None
    return printed[:80]
_MAX_BYTES = 20_000_000  # a furnished quarterly report runs to a few MB; anything far larger is no statement


def read_printed(ref, fiscal_year_end: str, year_offset: int, signs: dict | None = None,
                 document_end: date | None = None, summaries: list | None = None) -> tuple[list, list[str], list[str]]:
    """A filing without XBRL, read from its printed documents. A release's own period is its quarter (`document_end`).
    With `signs`, every figure its tables print is also collected there by label; with `summaries`, an annual report's
    multi-year summary figures (readers/printed/summary.py)."""
    found, notes, kinds = [], [], []
    release = ref.form == "6-K"
    if document_end is None and not release:
        raw_end = getattr(ref.filing, "period_of_report", None)
        try:
            document_end = date.fromisoformat(str(raw_end)[:10]) if raw_end else None
        except ValueError:
            document_end = None
    attachments = list(ref.filing.attachments)
    other = another_registrant(ref, attachments) if release else None
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
        reader = {"html": read_document, "text": read_text, "slides": read_slides}[printed]
        try:
            readings, document_notes = reader(content, accession=ref.accession, form=ref.form,
                                              filed=ref.filed, document_end=document_end,
                                              fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        except Exception as error:  # noqa: BLE001 - one unreadable document never stops the filing
            notes.append(f"{attachment.document}: unreadable ({type(error).__name__})")
            continue
        if signs is not None:
            parse = {"html": parse_html, "text": parse_text, "slides": parse_slides}[printed]
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
            unscaled.append((reader, content, basis, currency, attachment.document))
        for reading in readings:
            reading.basis = basis
            reading.currency = reading.currency or currency
        # One presentation per period and basis: ASML's 6-Ks file an IFRS interim report before the US GAAP release.
        held = {(reading.key, reading.basis) for reading in found}
        found.extend(reading for reading in readings if (reading.key, reading.basis) not in held)
        notes.extend(f"{attachment.document}: {note}" for note in document_notes)
        kinds.append(printed)
    for reader, content, basis, currency, document in unscaled:
        # Statements that print no scale, read only at a scale the filing's own figures prove.
        readings, proved = with_proven_scale(reader, content, schedules, list(found), accession=ref.accession,
                                             form=ref.form, filed=ref.filed, document_end=document_end,
                                             fiscal_year_end=fiscal_year_end, year_offset=year_offset)
        held = {(reading.key, reading.basis) for reading in found}
        for reading in readings:
            reading.basis, reading.currency = basis, reading.currency or currency
        found.extend(reading for reading in readings if (reading.key, reading.basis) not in held)
        notes.extend(f"{document}: {note}" for note in proved)
    for reading in found:  # the filing's own EX-27 schedules, an independent check on its printed figures
        reading.basis = reading.basis or filing_basis or default_basis(ref.form)
        reading.currency = reading.currency or filing_currency or default_currency(ref.form)
        check = schedule_check(reading, schedules)
        if check is not None:
            reading.checks.append(check)
    if schedules:
        kinds.append("ex27")
    return found, notes, sorted(set(kinds))
