"""SEC EDGAR access through edgartools: the company, the filings worth reading, and their documents."""
from __future__ import annotations

import os
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
from edgar import Company, set_identity
from edgar.exceptions import IdentityError, http_status
from edgar.httprequests import is_unreachable
from edgar.sgml import FilingSGML
from edgar.settings import get_identity

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
# How long to wait before each further try after a temporary SEC failure. Listing a company's filings is not guarded per
# filing, so one error page or timeout there used to fail the whole company for the run (KO, AMZN, APO in November 2026).
_SEC_WAITS = (5, 20, 60)
_TEMPORARY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_SUBMISSION_TIMEOUT = 120


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


def _temporary(error: Exception) -> bool:
    """A failure that trying again can cure: no answer, an error page, or a busy server. A rejected identity or a filing
    that does not exist would only fail again."""
    if isinstance(error, IdentityError):
        return False
    return is_unreachable(error) or http_status(error) in _TEMPORARY_STATUS


def _with_retries(read, read_again=None):
    """The result of `read`; after a temporary failure, of `read_again` (or `read`) a few more times, waiting between."""
    try:
        return read()
    except Exception as error:  # noqa: BLE001 - sorted by _temporary
        if not _temporary(error):
            raise
        failure = error
    for wait in _SEC_WAITS:
        print(f"SEC: {type(failure).__name__}; trying again in {wait} s", flush=True)
        time.sleep(wait)
        try:
            return (read_again or read)()
        except Exception as error:  # noqa: BLE001 - sorted by _temporary
            if not _temporary(error):
                raise
            failure = error
    raise failure


def _read_submission_afresh(filing) -> None:
    # Past edgartools' cache, which keeps anything read from /Archives for good and could hand back the same error page.
    response = httpx.get(filing.text_url, headers={"User-Agent": get_identity()}, timeout=_SUBMISSION_TIMEOUT,
                         follow_redirects=True)
    response.raise_for_status()
    submission = FilingSGML.from_text(response.text)
    # As FilingSGML.from_filing fills what a submission's header leaves out.
    if not submission.accession_number:
        submission.header.filing_metadata.update("ACCESSION NUMBER", filing.accession_no)
    if not submission.header.filing_metadata.get("CIK"):
        submission.header.filing_metadata.update("CIK", str(filing.cik).zfill(10))
    if not submission.header.form:
        submission.header.filing_metadata.update("CONFORMED SUBMISSION TYPE", filing.form)
    filing._sgml = submission


def load_submission(filing) -> None:
    """Holds a filing's full submission on it. Its URL, attachments and documents are read from the submission, so reading
    those afterwards cannot fail."""
    _with_retries(filing.sgml, lambda: _read_submission_afresh(filing))


def _document_url(filing, known_urls: dict[str, str] | None) -> str:
    """A filing's main document. Asking edgartools downloads the whole submission, so a filing already recorded keeps the
    address recorded: a run used to download every report of the last ten years to list them."""
    known = (known_urls or {}).get(filing.accession_no)
    if known:
        return known
    load_submission(filing)
    return filing.filing_url if hasattr(filing, "filing_url") else filing.url


def company_profile(symbol: str) -> tuple[CompanyProfile, Company]:
    return _with_retries(lambda: _company_profile(symbol))


def _company_profile(symbol: str) -> tuple[CompanyProfile, Company]:
    company = Company(symbol)
    latest = _latest_annual_report(company)
    fiscal_year_end = annual_report_year_end(company, str(getattr(company, "fiscal_year_end", None) or "1231").zfill(4), latest)
    profile = CompanyProfile(
        symbol=symbol,
        cik=int(company.cik),
        name=str(company.name),
        fiscal_year_end=fiscal_year_end,
        # A Canadian issuer filing 40-F (Bank of Nova Scotia, TD) is not "foreign" to edgartools, yet furnishes its
        # results on 6-K and never on 8-K: its annual report's form decides.
        foreign=bool(getattr(company, "is_foreign", False)) or str(getattr(latest, "form", "")) in ("20-F", "40-F"),
    )
    return profile, company


def _latest_annual_report(company: Company):
    try:
        return company.get_filings(form=["10-K", "20-F", "40-F"]).latest(1)
    except Exception:  # noqa: BLE001 - no annual report yet, or SEC unreachable
        return None


def annual_report_year_end(company: Company, stated: str, latest=False) -> str:
    """The fiscal year end, checked against the newest annual report's period: SEC's own record can be wrong (Sun
    Communities' "0513" for a December year, which failed every quarter's label). A year end within ten days of the
    report's stands (a 52/53-week year moves a few days); a further one gives way to the report's month and day."""
    try:
        latest = company.get_filings(form=["10-K", "20-F", "40-F"]).latest(1) if latest is False else latest
        period = date.fromisoformat(str(getattr(latest, "period_of_report", "") or "")[:10])
    except Exception:  # noqa: BLE001 - no annual report yet, or SEC unreachable: keep what SEC states
        return stated
    return stated if year_ends_agree(stated, period) else period.strftime("%m%d")


def year_ends_agree(stated: str, period: date) -> bool:
    """Whether a stated MMDD year end is within ten days of a period end, across a year boundary too (1231 and 0102)."""
    try:
        anchor = date(period.year, int(stated[:2]), int(stated[2:]))
    except ValueError:
        return False
    return any(abs((anchor.replace(year=anchor.year + shift) - period).days) <= 10 for shift in (-1, 0, 1))


def periodic_reports(company: Company, since: date, known_urls: dict[str, str] | None = None) -> list[FilingRef]:
    filings = _with_retries(lambda: company.get_filings(form=list(PERIODIC_FORMS), filing_date=f"{since.isoformat()}:"))
    refs = []
    for filing in filings:
        if filing.form not in PERIODIC_FORMS:
            continue  # amendments restate a subset; the original report is the complete one
        refs.append(FilingRef(
            accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="periodic_report",
            source_url=_document_url(filing, known_urls), filing=filing,
        ))
    return refs


def report_period_ends(company: Company, since: date) -> tuple[date, ...]:
    """The period each 10-Q and 10-K covers, from the filing index (no document is read): the company's own quarter
    ends, which a week-based calendar puts weeks before month end. Empty for a foreign filer or when the index says none."""
    try:
        filings = _with_retries(lambda: company.get_filings(form=list(PERIODIC_FORMS), filing_date=f"{(since - timedelta(days=400)).isoformat()}:"))
    except Exception:  # noqa: BLE001 - the month-end guess still works without them
        return ()
    ends = set()
    for filing in filings:
        raw = getattr(filing, "period_of_report", None) or getattr(filing, "report_date", None)
        try:
            ends.add(raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10]))
        except ValueError:
            continue
    return tuple(sorted(ends))


def earnings_releases(company: Company, profile: CompanyProfile, since: date, archive=None,
                      passed: list[FilingRef] | None = None, known_urls: dict[str, str] | None = None) -> list[FilingRef]:
    """Earnings documents and candidates. 8-K Item 2.02 exhibits are confirmed earnings releases. Other filings with
    exhibits furnished within 75 days of a quarter end are candidates for the AI to classify: a U.S. filer's 8-K when
    that quarter has no Item 2.02 release (results furnished under another item), and every foreign filer's 6-K (the
    earnings release, the quarterly financial report, or unrelated notices). A recent foreign filer's 6-K that is no
    candidate (no exhibit, or outside the window) goes to `passed`, so it is recorded as looked at."""
    form = "6-K" if profile.foreign else "8-K"
    own_ends = () if profile.foreign else report_period_ends(company, since)
    filings = sorted(_with_retries(lambda: company.get_filings(form=form, filing_date=f"{since.isoformat()}:")),
                     key=lambda item: item.filing_date)
    confirmed_quarters = set()
    refs: list[FilingRef] = []
    candidates = []
    for filing in filings:
        if filing.form != form:
            continue
        quarter_end = nominal_quarter_end_before(filing.filing_date, profile.fiscal_year_end, own_ends=own_ends)
        in_window = _EARNINGS_WINDOW[0] <= filing.filing_date - quarter_end <= _EARNINGS_WINDOW[1]
        item_202 = not profile.foreign and "2.02" in str(getattr(filing, "items", "") or "")
        if item_202 or in_window:
            candidates.append((filing, item_202))
        elif passed is not None and profile.foreign and filing.filing_date >= date.today() - PASSED_OVER_DAYS:
            passed.append(_passed_ref(filing, known_urls))
    # Each candidate's exhibits come from the archive (or SEC), a few at a time rather than one after another.
    with ThreadPoolExecutor(max_workers=_LISTING_READERS) as pool:
        found = list(pool.map(lambda candidate: _archived_exhibits(candidate[0], archive), candidates))
    for (filing, item_202), exhibits in zip(candidates, found):
        if not exhibits:
            if passed is not None and profile.foreign and filing.filing_date >= date.today() - PASSED_OVER_DAYS:
                passed.append(_passed_ref(filing, known_urls))
            continue
        ref = _release_ref(filing, exhibits, profile, own_ends)
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
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw[:200_000]))).strip()
        if is_own_document(exhibit):
            text = text[_after_cover(text):]  # the report, not the form's cover page and signature
        parts.append(f"{exhibit.document_type} {exhibit.document} ({getattr(exhibit, 'description', '') or ''}): {text[:chars]}")
    return f"Form {ref.form} filed {ref.filed}\n" + "\n\n".join(parts)


def is_own_document(attachment) -> bool:
    """A 6-K's own document read in place of exhibits it does not have."""
    return str(getattr(attachment, "document_type", "") or "").upper() == "6-K"


# A 6-K's cover ends with its signature: "By /s/ Lora Ho … Senior Vice President & Chief Financial Officer".
_SIGNED = re.compile(r"By:?\s*/s/.{0,120}\b(?:Officer|Director|Secretary|President|Manager|Counsel)\b", re.I)


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
    load_submission(filing)
    attachments = list(filing.attachments)
    exhibits = [attachment for attachment in attachments
                if str(attachment.document_type or "").upper().startswith("EX-99") and attachment.is_html()]
    if exhibits or filing.form != "6-K":
        return exhibits
    return [attachment for attachment in attachments
            if str(attachment.document_type or "").upper() == "6-K" and attachment.is_html()][:1]


def _passed_ref(filing, known_urls: dict[str, str] | None = None) -> FilingRef:
    return FilingRef(
        accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
        source_url=_document_url(filing, known_urls), filing=filing, confirmed=False,
    )


def _release_ref(filing, exhibits: list, profile: CompanyProfile, own_ends: tuple = ()) -> FilingRef:
    return FilingRef(
        accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
        source_url=exhibits[0].url,
        period_end=nominal_quarter_end_before(filing.filing_date, profile.fiscal_year_end, own_ends=own_ends),
        exhibits=exhibits, filing=filing,
    )


# A listing's final prospectus: 424B4 for most IPOs, 424B1 for some.
PROSPECTUS_FORMS = ("424B4", "424B1")
# A company is a recent listing while its first periodic report is this young; its prospectus came at most a year before.
_RECENT_LISTING = timedelta(days=730)
_PROSPECTUS_BEFORE = timedelta(days=400)


def ipo_prospectus(company: Company, first_report: date | None, today: date | None = None) -> FilingRef | None:
    """The final prospectus of a recent listing (SpaceX's 424B4 of June 2026): its history before the first 10-Q.
    None for a company listed longer, whose reports reach back on their own."""
    if first_report is None or first_report < (today or date.today()) - _RECENT_LISTING:
        return None
    filings = _with_retries(lambda: company.get_filings(
        form=list(PROSPECTUS_FORMS), filing_date=f"{(first_report - _PROSPECTUS_BEFORE).isoformat()}:{first_report.isoformat()}"))
    filings = sorted((filing for filing in filings if filing.form in PROSPECTUS_FORMS), key=lambda item: item.filing_date)
    if not filings:
        return None
    filing = filings[-1]
    load_submission(filing)
    document = next((attachment for attachment in filing.attachments
                     if str(attachment.document_type or "").upper() == filing.form and attachment.is_html()), None)
    if document is None:
        return None
    return FilingRef(accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
                     source_url=document.url, exhibits=[document], filing=filing)
