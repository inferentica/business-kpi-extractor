"""SEC EDGAR access through edgartools: the company, the filings worth reading, and their documents."""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from edgar import Company, set_identity

from .archive import exhibits_from_record, exhibits_record, filing_folder
from .fiscal import nominal_quarter_end_before

PERIODIC_FORMS = ("10-Q", "10-K", "20-F", "40-F")
_EARNINGS_WINDOW = (timedelta(days=5), timedelta(days=75))
# Quarterly financial reports furnished on 6-K run to ~10 MB of HTML; only their revenue sections reach the AI.
_MAX_EXHIBIT_BYTES = 20_000_000
_LISTING_READERS = 6
# Recent 6-Ks that are not earnings documents (monthly revenue, buyback notices) are recorded as looked at, so the
# discovery pass, which counts a filing from the last 30 days that is not recorded as new, does not start runs for them.
PASSED_OVER_DAYS = timedelta(days=35)


def configure_identity() -> None:
    set_identity(os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com"))


@dataclass
class CompanyProfile:
    symbol: str
    cik: int
    name: str
    fiscal_year_end: str
    foreign: bool


@dataclass
class FilingRef:
    accession: str
    form: str
    filed: date
    role: str  # periodic_report | earnings_release
    source_url: str
    period_end: date | None = None
    exhibits: list = field(default_factory=list, repr=False)
    filing: object = field(default=None, repr=False)
    # An 8-K Item 2.02 is an earnings release by definition; anything else is classified by the AI first.
    confirmed: bool = True


def company_profile(symbol: str) -> tuple[CompanyProfile, Company]:
    company = Company(symbol)
    fiscal_year_end = str(getattr(company, "fiscal_year_end", None) or "1231").zfill(4)
    profile = CompanyProfile(
        symbol=symbol,
        cik=int(company.cik),
        name=str(company.name),
        fiscal_year_end=fiscal_year_end,
        foreign=bool(getattr(company, "is_foreign", False)),
    )
    return profile, company


def periodic_reports(company: Company, since: date) -> list[FilingRef]:
    filings = company.get_filings(form=list(PERIODIC_FORMS), filing_date=f"{since.isoformat()}:")
    refs = []
    for filing in filings:
        if filing.form not in PERIODIC_FORMS:
            continue  # amendments restate a subset; the original report is the complete one
        refs.append(FilingRef(
            accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="periodic_report",
            source_url=filing.filing_url if hasattr(filing, "filing_url") else filing.url, filing=filing,
        ))
    return refs


def earnings_releases(company: Company, profile: CompanyProfile, since: date, archive=None,
                      passed: list[FilingRef] | None = None) -> list[FilingRef]:
    """Earnings documents and candidates. 8-K Item 2.02 exhibits are confirmed earnings releases. Other filings with
    exhibits furnished within 75 days of a quarter end are candidates for the AI to classify: a U.S. filer's 8-K when
    that quarter has no Item 2.02 release (results furnished under another item), and every foreign filer's 6-K (the
    earnings release, the quarterly financial report, or unrelated notices). A recent foreign filer's 6-K that is no
    candidate (no exhibit, or outside the window) goes to `passed`, so it is recorded as looked at."""
    form = "6-K" if profile.foreign else "8-K"
    filings = sorted(company.get_filings(form=form, filing_date=f"{since.isoformat()}:"), key=lambda item: item.filing_date)
    confirmed_quarters = set()
    refs: list[FilingRef] = []
    candidates = []
    for filing in filings:
        if filing.form != form:
            continue
        quarter_end = nominal_quarter_end_before(filing.filing_date, profile.fiscal_year_end)
        in_window = _EARNINGS_WINDOW[0] <= filing.filing_date - quarter_end <= _EARNINGS_WINDOW[1]
        item_202 = not profile.foreign and "2.02" in str(getattr(filing, "items", "") or "")
        if item_202 or in_window:
            candidates.append((filing, item_202))
        elif passed is not None and profile.foreign and filing.filing_date >= date.today() - PASSED_OVER_DAYS:
            passed.append(_passed_ref(filing))
    # Each candidate's exhibits come from the archive (or SEC), a few at a time rather than one after another.
    with ThreadPoolExecutor(max_workers=_LISTING_READERS) as pool:
        found = list(pool.map(lambda candidate: _archived_exhibits(candidate[0], archive), candidates))
    for (filing, item_202), exhibits in zip(candidates, found):
        if not exhibits:
            if passed is not None and profile.foreign and filing.filing_date >= date.today() - PASSED_OVER_DAYS:
                passed.append(_passed_ref(filing))
            continue
        ref = _release_ref(filing, exhibits, profile)
        ref.confirmed = item_202
        if item_202:
            confirmed_quarters.add(ref.period_end)
        refs.append(ref)
    return [ref for ref in refs if ref.confirmed or ref.period_end not in confirmed_quarters]


def classification_excerpt(ref: FilingRef, chars: int = 1_500) -> str:
    """What the classifier sees: each exhibit's name and description, and the opening of its text."""
    parts = []
    for exhibit in ref.exhibits[:4]:
        raw = exhibit_html(exhibit) or ""
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw[:200_000])).strip()
        if is_own_document(exhibit):
            text = text[_after_cover(text):]  # the report, not the form's cover page and signature
        parts.append(f"{exhibit.document_type} {exhibit.document} ({getattr(exhibit, 'description', '') or ''}): {text[:chars]}")
    return f"Form {ref.form} filed {ref.filed}\n" + "\n\n".join(parts)


def is_own_document(attachment) -> bool:
    """A 6-K's own document read in place of exhibits it does not have."""
    return str(getattr(attachment, "document_type", "") or "").upper() == "6-K"


# A 6-K's cover ends with its signature: "By /s/ Lora Ho … Senior Vice President & Chief Financial Officer".
_SIGNED = re.compile(r"By:?\s*/s/.{0,200}?\b(?:Officer|Director|Secretary|President|Manager|Counsel)\b", re.I)


def _after_cover(text: str) -> int:
    match = _SIGNED.search(text[:6_000])
    return match.end() if match else 0


def exhibit_html(attachment) -> str | None:
    content = attachment.content
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    if not content or len(content) > _MAX_EXHIBIT_BYTES:
        return None
    return content


# The archive's record of a filing's documents; "exhibits-2" since a 6-K without exhibits counts its own document.
_DOCUMENTS_RECORD = "exhibits-2.json"


def _archived_exhibits(filing, archive) -> list:
    """A filing's HTML exhibits from the archive, else from EDGAR and then archived (an empty list too, so a filing
    with none is not fetched again)."""
    if archive is None:
        return _html_exhibits(filing)
    folder = filing_folder(filing.filing_date, filing.form, filing.accession_no)
    stored = archive.read_json(folder, _DOCUMENTS_RECORD)
    if stored is not None:
        return exhibits_from_record(stored)
    exhibits = _html_exhibits(filing)
    archive.write_json(folder, _DOCUMENTS_RECORD, exhibits_record(exhibits, _MAX_EXHIBIT_BYTES))
    return exhibits


def _html_exhibits(filing) -> list:
    """A filing's HTML exhibits. A 6-K with none carries its content in its own document (TSMC's until October 2019:
    its results and quarterly financial statements, by technology from 2018), which then stands in for them."""
    attachments = list(filing.attachments)
    exhibits = [attachment for attachment in attachments
                if str(attachment.document_type or "").upper().startswith("EX-99") and attachment.is_html()]
    if exhibits or filing.form != "6-K":
        return exhibits
    return [attachment for attachment in attachments
            if str(attachment.document_type or "").upper() == "6-K" and attachment.is_html()][:1]


def _passed_ref(filing) -> FilingRef:
    return FilingRef(
        accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
        source_url=filing.filing_url if hasattr(filing, "filing_url") else filing.url, filing=filing, confirmed=False,
    )


def _release_ref(filing, exhibits: list, profile: CompanyProfile) -> FilingRef:
    return FilingRef(
        accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
        source_url=exhibits[0].url,
        period_end=nominal_quarter_end_before(filing.filing_date, profile.fiscal_year_end),
        exhibits=exhibits, filing=filing,
    )
