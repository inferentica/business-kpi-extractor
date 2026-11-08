"""SEC EDGAR through edgartools, behind the source adapter (base.FilingSource): the company, the filings worth reading,
their documents and their XBRL. `SEC` is the adapter the pipeline uses; the functions below are its parts."""
from __future__ import annotations

import os
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
from edgar import Company, set_identity
from edgar.entity.submissions import create_entity_from_submissions_json
from edgar.exceptions import IdentityError, http_status
from edgar.httprequests import is_unreachable
from edgar.sgml import FilingSGML
from edgar.settings import get_identity

from .archive import exhibits_from_record, exhibits_record, filing_folder, xbrl_from_parts, xbrl_parts
from .base import CompanyProfile, FilingRef, SourceCompany
from ..domain.fiscal import nominal_quarter_end_before

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
    _hold_submission(filing, response.text)


def _hold_submission(filing, text: str) -> None:
    """Holds a full submission's text on its filing, as edgartools would after reading it from SEC."""
    submission = FilingSGML.from_text(text)
    # As FilingSGML.from_filing fills what a submission's header leaves out.
    if not submission.accession_number:
        submission.header.filing_metadata.update("ACCESSION NUMBER", filing.accession_no)
    if not submission.header.filing_metadata.get("CIK"):
        submission.header.filing_metadata.update("CIK", str(filing.cik).zfill(10))
    if not submission.header.form:
        submission.header.filing_metadata.update("CONFORMED SUBMISSION TYPE", filing.form)
    filing._sgml = submission


def load_submission(filing, archive=None) -> None:
    """Holds a filing's full submission on it. Its URL, attachments and documents are read from the submission, so reading
    those afterwards cannot fail. The archive's copy comes first (the filing feed's worker puts each new filing there),
    then SEC."""
    if getattr(filing, "_sgml", None) is not None:
        return
    if archive is not None:
        text = archive.read_submission(filing.accession_no)
        if text:
            try:
                _hold_submission(filing, text)
                return
            except Exception:  # noqa: BLE001 - a damaged copy: SEC's then
                pass
    _with_retries(filing.sgml, lambda: _read_submission_afresh(filing))


def _document_url(filing, known_urls: dict[str, str] | None, archive=None) -> str:
    """A filing's main document. Asking edgartools downloads the whole submission, so a filing already recorded keeps the
    address recorded: a run used to download every report of the last ten years to list them."""
    known = (known_urls or {}).get(filing.accession_no)
    if known:
        return known
    load_submission(filing, archive)
    return filing.filing_url if hasattr(filing, "filing_url") else filing.url


def company_profile(symbol: str) -> tuple[CompanyProfile, Company]:
    return _with_retries(lambda: _company_profile(symbol))


def _sic(company) -> int | None:
    try:
        return int(str(getattr(company, "sic", None)).strip())
    except (TypeError, ValueError):
        return None


def _company_profile(symbol: str) -> tuple[CompanyProfile, Company]:
    return profile_of(symbol, Company(symbol))


def archived_company(symbol: str, archive) -> tuple[CompanyProfile, Company] | None:
    """The company from the filing list Bedrock's worker keeps in the archive, so opening it asks SEC nothing (SEC
    refuses many requests from shared cloud addresses). None, with the reason logged, when that list cannot stand in
    for SEC's own; the company is then read from SEC."""
    listing, reason = archive.filing_list()
    if listing is None:
        archive.log(f"{symbol}: {reason}; listing filings from SEC")
        return None
    try:
        company = create_entity_from_submissions_json(listing, entity_type="company")
        return profile_of(symbol, company)
    except Exception as error:  # noqa: BLE001 - a list edgartools cannot read: SEC's instead
        archive.log(f"{symbol}: archived filing list unreadable ({str(error)[:200]}); listing filings from SEC")
        return None


def profile_of(symbol: str, company: Company) -> tuple[CompanyProfile, Company]:
    latest = _latest_annual_report(company)
    fiscal_year_end = annual_report_year_end(company, str(getattr(company, "fiscal_year_end", None) or "1231").zfill(4), latest)
    profile = CompanyProfile(
        symbol=symbol,
        cik=int(company.cik),
        name=str(company.name),
        fiscal_year_end=fiscal_year_end,
        foreign=files_as_foreign(company, latest),
        sic=_sic(company),
    )
    return profile, company


def files_as_foreign(company: Company, latest) -> bool:
    """Whether the company furnishes results on 6-K (a foreign private issuer) or files 8-Ks like a U.S. company: its
    annual report's form decides. edgartools goes by incorporation, so a 40-F filer (Bank of Nova Scotia) read as
    domestic and found no 8-Ks, and a 10-K filer incorporated abroad (JFrog in Israel, Tronox a UK plc) as foreign and
    found no 6-Ks. Only a company with no annual report yet keeps edgartools' answer."""
    form = str(getattr(latest, "form", "") or "")
    if form in ("20-F", "40-F"):
        return True
    if form == "10-K":
        return False
    return bool(getattr(company, "is_foreign", False))


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


def periodic_reports(company: Company, since: date, known_urls: dict[str, str] | None = None, archive=None) -> list[FilingRef]:
    filings = _with_retries(lambda: company.get_filings(form=list(PERIODIC_FORMS), filing_date=f"{since.isoformat()}:"))
    refs = []
    for filing in filings:
        if filing.form not in PERIODIC_FORMS:
            continue  # amendments restate a subset; the original report is the complete one
        refs.append(FilingRef(
            accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="periodic_report",
            source_url=_document_url(filing, known_urls, archive), filing=filing,
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
    exhibits = _html_exhibits(filing, archive)
    archive.write_json(folder, _DOCUMENTS_RECORD, exhibits_record(exhibits, _MAX_EXHIBIT_BYTES))
    return exhibits


def _html_exhibits(filing, archive=None) -> list:
    """A filing's HTML exhibits. A 6-K with none carries its content in its own document (TSMC's until October 2019:
    its results and quarterly financial statements, by technology from 2018), which then stands in for them."""
    load_submission(filing, archive)
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


def release_period(filed: date, fiscal_year_end: str, own_ends: tuple = ()) -> date:
    """The quarter a furnished filing reports. Usually the latest quarter end at least five days before it; but a
    filing days after a quarter closes (Tesla's production and deliveries, two days after) is about that quarter, not
    the one before it: no results come more than the earnings window after their quarter."""
    usual = nominal_quarter_end_before(filed, fiscal_year_end, own_ends=own_ends)
    if filed - usual <= _EARNINGS_WINDOW[1]:
        return usual
    latest = nominal_quarter_end_before(filed, fiscal_year_end, min_gap_days=0, own_ends=own_ends)
    return latest if latest > usual else usual


def _release_ref(filing, exhibits: list, profile: CompanyProfile, own_ends: tuple = ()) -> FilingRef:
    return FilingRef(
        accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="earnings_release",
        source_url=exhibits[0].url,
        period_end=release_period(filing.filing_date, profile.fiscal_year_end, own_ends),
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


_NO_XBRL = {"version": 1, "parts": {}}


class SecSource:
    """SEC EDGAR as a FilingSource."""

    name = "sec"
    url_prefixes = ("https://www.sec.gov/",)

    def open_company(self, symbol: str, archive=None) -> SourceCompany:
        opened = archived_company(symbol, archive) if archive is not None else None
        profile, company = opened or company_profile(symbol)
        return SourceCompany(profile, company)

    def periodic_reports(self, company: SourceCompany, since: date, known_urls: dict[str, str] | None = None,
                         archive=None) -> list[FilingRef]:
        return periodic_reports(company.handle, since, known_urls, archive)

    def earnings_releases(self, company: SourceCompany, since: date, archive=None, passed: list[FilingRef] | None = None,
                          known_urls: dict[str, str] | None = None) -> list[FilingRef]:
        return earnings_releases(company.handle, company.profile, since, archive, passed, known_urls)

    def prospectus(self, company: SourceCompany, first_report: date | None) -> FilingRef | None:
        return ipo_prospectus(company.handle, first_report)

    def report_html(self, ref: FilingRef, archive=None) -> str:
        load_submission(ref.filing, archive)
        return ref.filing.html()

    def exhibit_html(self, exhibit) -> str | None:
        return exhibit_html(exhibit)

    def is_own_document(self, exhibit) -> bool:
        return is_own_document(exhibit)

    def classification_excerpt(self, ref: FilingRef) -> str:
        return classification_excerpt(ref)

    def archive_tagged_facts(self, ref: FilingRef, archive) -> None:
        # A filing without an XBRL instance is marked, so it is not downloaded again on every run.
        load_submission(ref.filing, archive)
        archive.write_json(filing_folder(ref.filed, ref.form, ref.accession), "xbrl.json", xbrl_parts(ref.filing) or _NO_XBRL)

    def tagged_facts(self, ref: FilingRef, archive=None):
        """A filing's XBRL from the archive, else from EDGAR and then archived."""
        if archive is None:
            load_submission(ref.filing)
            return ref.filing.xbrl()
        folder = filing_folder(ref.filed, ref.form, ref.accession)
        stored = archive.read_json(folder, "xbrl.json")
        if stored and stored.get("parts"):
            return xbrl_from_parts(stored)
        load_submission(ref.filing, archive)
        if stored is not None:
            return ref.filing.xbrl()  # marked as having no instance to archive: EDGAR's own reading, as before
        parts = xbrl_parts(ref.filing)
        if parts is None:
            archive.write_json(folder, "xbrl.json", _NO_XBRL)
            return ref.filing.xbrl()
        archive.write_json(folder, "xbrl.json", parts)
        return xbrl_from_parts(parts)


SEC = SecSource()
