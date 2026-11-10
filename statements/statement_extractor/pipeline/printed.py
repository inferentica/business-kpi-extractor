"""A filing's statements read from its printed documents: the report itself and an annual report exhibit (EX-13) for a
periodic report, the furnished exhibits (EX-99) for a results release, in HTML (about 2001 onwards) or plain text
(about 1993-2000). Each document's basis is the one it states; its EX-27 schedules check what it prints."""
from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher

from datetime import date

from ..checks.schedules import parse_schedule, schedule_check
from ..domain.basis import (BARE_DOLLARS, BARE_PESOS, default_basis, default_currency, same_kind, stated_basis,
                             stated_currency)
from ..domain.calendar import ANNUAL_FORMS
from ..readers.html.parse import parse_html
from ..readers.html.read import read_document
from ..readers.printed.sign_index import printed_figures
from ..readers.printed.summary import read_summaries
from ..readers.positioned.layout import is_positioned, positioned_text
from ..readers.printed.titled import headed_kinds, split_titled
from ..readers.slides.parse import is_slides, parse_slides
from ..readers.slides.read import read_document as read_slides
from ..readers.text.parse import parse_text
from ..readers.text.read import read_document as read_text
from ..sources.documents import PRINTED_DOCUMENTS, release_documents
from .unstated_scale import with_proven_scale

_ASSET_BACKED = ("EX-33", "EX-34", "EX-35")
_REGISTRANT = re.compile(r"\(\s*(?:Translation\s+of\s+registrant|Exact\s+name\s+of\s+registrant)", re.I)
_BEFORE_NAME = re.compile(r".*(?:\d{1,3}-\d{4,6}|\b(?:19|20)\d{2}\b|\b1934\b|\bForm\s+6-K\b)\s*:?", re.I | re.S)
_SUFFIXES = {"THE", "PLC", "INC", "CORP", "CORPORATION", "LTD", "LIMITED", "CO", "COMPANY", "SA", "AG", "NV", "LLC", "ON"}
_NAMES: dict[str, list[str]] = {}


def _words(name: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", html.unescape(name)).encode("ascii", "ignore").decode()  # "PETRÓLEO"
    return {word for word in re.sub(r"[^A-Z0-9]+", " ", plain.upper()).split() if word not in _SUFFIXES}


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


def _cover_text(attachments) -> str | None:
    """A 6-K cover's first words as plain text."""
    cover = next((item for item in attachments if str(item.document_type or "").upper().startswith("6-K")), None)
    if cover is None:
        return None
    content = cover.content
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            content = content.decode("latin-1")  # Petrobras's covers
    text = re.sub(r"<[^>]+>|&nbsp;", " ", html.unescape(str(content or "")) if "&" in str(content or "")[:30_000] else str(content or ""))
    return re.sub(r"\s+", " ", text[:30_000])


def another_registrant(ref, attachments) -> str | None:
    """The registrant a 6-K's cover names, when it is neither the company nor a name the company once had."""
    text = _cover_text(attachments)
    if text is None:
        return None
    match = _REGISTRANT.search(text)
    if match is None:
        return None
    printed = _BEFORE_NAME.sub("", text[max(0, match.start() - 150):match.start()]).strip(" :.,")
    words = _words(printed)
    names = [_words(name) for name in _company_names(ref.filing)]
    if not words or not names or any(words <= name or name <= words or "".join(sorted(words)) == "".join(sorted(name))
                                     or any(SequenceMatcher(None, "".join(_ordered(printed)), "".join(_ordered(other))).ratio() >= 0.85
                                            for other in _company_names(ref.filing))  # "PETR?LEO": one letter lost
                                     for name in names if name):
        return None
    # Only a name of the company's own family: a subsidiary shares its brand (Lloyds Bank plc beside Lloyds Banking
    # Group); a name sharing nothing is the company's in another language (Copel's "Energy Company of Paraná").
    return printed[:80] if any(words & name for name in names) else None


_GENERIC = {"AND", "OF", "DE", "DO", "DA", "DAS", "DOS", "THE", "HOLDINGS", "HOLDING", "GROUP", "INCORPORATED"}


def _other_companies(readings: list, ref, cover: str | None) -> list[str]:
    """The names of other companies that a release's statement titles name: an acquired company's statements furnished
    with the company's report (Gildan's 6-K of June 2011 carries Gold Toe Moretz's, "GOLD TOE MORETZ HOLDINGS CORP. AND
    SUBSIDIARIES CONSOLIDATED BALANCE SHEETS"). A name is the company's own when it shares a word with the company's
    EDGAR name or a former one, or with the registrant's names on the 6-K's cover: FEMSA's titles print "FOMENTO
    ECONÓMICO MEXICANO", EDGAR "MEXICAN ECONOMIC DEVELOPMENT INC". Only names of two words or more are compared; an
    annual report, with no cover to give its own names, is never checked."""
    if cover is None:
        return []
    own = set().union(*(_named(name) for name in _company_names(ref.filing)),
                      _named(cover[:cover.lower().find("indicate by check mark")]))
    return sorted(printed for printed in {reading.titled for reading in readings if reading.titled}
                  if len(_named(printed)) >= 2 and not _named(printed) & own)


def _named(name: str) -> set[str]:
    return {word for word in _words(name) - _GENERIC if len(word) >= 3}


def _ordered(name: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", html.unescape(name)).encode("ascii", "ignore").decode()
    return [word for word in re.sub(r"[^A-Z0-9]+", " ", plain.upper()).split() if word not in _SUFFIXES]
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
        others = _other_companies(readings, ref, cover)
        if others:
            readings = [reading for reading in readings if reading.titled not in others]
            document_notes.append(f"another company's statements left out ({'; '.join(sorted(others))[:120]})")
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
