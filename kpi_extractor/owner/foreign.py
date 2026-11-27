"""A foreign filer's 6-Ks for an owner-built company, told apart by code, never by the AI.

Arm files each quarter's financial statements on a 6-K of their own, with full XBRL, and its results on a second 6-K
the same day (a shareholder letter behind a one-page Exhibit 99.1). The listing marks every 6-K a candidate for the AI
to classify; an owner-built company takes no AI, so these two rules stand in for it:

- an interim report is a 6-K with XBRL whose report date is a quarter end before its filing date (other 6-Ks, such as
  AGM results or appointments, carry their filing date as the report date);
- so is a 6-K without XBRL whose exhibits open as an interim report: an operating and financial review (or MD&A)
  beside an index to its condensed financial statements, titled for the quarter it covers ("Three and Nine Months
  Ended September 30, 2025 and 2026"). Nebius files one each quarter beside its results 6-K, often the same day;
- a results release is a 6-K, not itself an interim report, that prints the total revenue its period's report tags:
  the quarter's, or for the fiscal year's 20-F the year's or the year less the third quarter's nine months. A 6-K the
  report has not yet confirmed (a fourth-quarter letter weeks before its 20-F) waits for the run after the report.
"""
from __future__ import annotations

import html
import re
from dataclasses import replace
from datetime import date, timedelta

from ..sources.base import FilingRef

_REPORT_LAG = timedelta(days=10)  # an interim report comes at least this long after its quarter
_SAME_PERIOD = timedelta(days=10)
_REPORT_AFTER = timedelta(days=60)  # a report confirms a release filed up to this long before it
_REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax")


def is_interim_report(filing) -> bool:
    if str(getattr(filing, "form", "")).upper() != "6-K" or not getattr(filing, "is_xbrl", False):
        return False
    try:
        reported = date.fromisoformat(str(getattr(filing, "report_date", "") or "")[:10])
    except ValueError:
        return False
    month_end = (reported + timedelta(days=1)).day == 1
    return month_end and filing.filing_date - reported >= _REPORT_LAG


def interim_ref(filing, source_url: str) -> FilingRef:
    end = date.fromisoformat(str(filing.report_date)[:10])
    return FilingRef(accession=filing.accession_no, form=filing.form, filed=filing.filing_date, role="periodic_report",
                     source_url=source_url, period_end=end, filing=filing)


# An exhibit's opening, past the page furniture: "Table of Contents", "Exhibit 99.1".
_FURNITURE = re.compile(r"^\s*(?:table of contents\s*)?(?:exhibit\s+99(?:\.\d+)?\s*)?", re.I)
_REVIEW = re.compile(r"operating and financial review|management['’]s discussion and analysis", re.I)
_STATEMENTS = re.compile(r"(?:index to )?(?:the )?(?:unaudited )?(?:interim )?condensed (?:consolidated )?(?:interim )?"
                         r"financial statements", re.I)
_OPENING = 300
_TITLE = re.compile(r"\bended\s+(?P<month>january|february|march|april|may|june|july|august|september|october|november|"
                    r"december)\s+(?P<day>\d{1,2}),?\s+(?P<year>(?:19|20)\d{2})(?:\s+and\s+(?P<other>(?:19|20)\d{2}))?\b", re.I)
_MONTHS = ("january february march april may june july august september october november december").split()


def _opening(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text or ""))
    return text[_FURNITURE.match(text).end():][:_OPENING]


def review_period(texts: list[str]) -> date | None:
    """The quarter end an interim report without XBRL covers, or None for any other 6-K: one exhibit opens as its review
    and one as its financial statements, and the review's title names the quarter."""
    openings = [_opening(text) for text in texts]
    # The review's title leads its exhibit; the statements' index may follow the filer's name ("NEBIUS GROUP N.V. INDEX
    # TO UNAUDITED CONDENSED CONSOLIDATED FINANCIAL STATEMENTS").
    review = next((opening for opening in openings if _REVIEW.match(opening)), None)
    if review is None or not any(_STATEMENTS.search(opening[:120]) for opening in openings):
        return None
    title = _TITLE.search(review)
    if title is None:
        return None
    try:
        # The later year is the period's ("June 30, 2025 and 2024" covers 2025, as "2024 and 2025" does).
        year = max(int(title["year"]), int(title["other"] or 0))
        return date(year, _MONTHS.index(title["month"].lower()) + 1, int(title["day"]))
    except ValueError:
        return None


def interim_reviews(candidates: list[FilingRef], reports: list[FilingRef], texts_of) -> list[FilingRef]:
    """The candidate 6-Ks (no XBRL) that are interim reports, as their quarter's report: read like a 10-Q, never as a
    release, so the results 6-K beside one does not crowd it out."""
    interim = {ref.accession for ref in reports}
    found = []
    for ref in candidates:
        if ref.form.upper() != "6-K" or ref.accession in interim or not ref.exhibits:
            continue
        end = review_period(texts_of(ref))
        if end is not None and (ref.filed - end) >= _REPORT_LAG:
            found.append(replace(ref, role="periodic_report", period_end=end, confirmed=True))
    return found


def _revenue(facts, end: date, days: tuple[int, int]) -> float | None:
    for concept in _REVENUE:
        for fact in facts or []:
            if (fact.concept == concept and not fact.dims and fact.start and fact.end == end
                    and days[0] <= (fact.end - fact.start).days <= days[1]):
                return fact.value
    return None


def _totals(report: FilingRef, reports: list[FilingRef], facts_of) -> list[float]:
    facts = facts_of(report)
    if report.form.upper().startswith(("20-F", "40-F")):
        year = _revenue(facts, report.period_end, (350, 380))
        q3 = next((r for r in reports if r.form.upper() == "6-K" and r.period_end
                   and 80 <= (report.period_end - r.period_end).days <= 100), None)
        nine = _revenue(facts_of(q3), q3.period_end, (260, 285)) if q3 else None
        return [v for v in (year, year - nine if year is not None and nine is not None else None) if v is not None]
    quarter = _revenue(facts, report.period_end, (80, 100))
    return [quarter] if quarter is not None else []


def prints_total(text: str, value: float) -> bool:
    """The text prints this total revenue in millions ("Total revenue (2) $1,443")."""
    figure = re.escape(f"{round(value / 1e6):,}")
    return re.search(rf"total revenues?\b(?:\s*\(\d\))*[^0-9]{{0,30}}?{figure}(?![\d,]|\.\d)", text, re.I) is not None


def plain_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def results_releases(candidates: list[FilingRef], reports: list[FilingRef], facts_of, text_of) -> list[FilingRef]:
    """The candidates that are results releases, confirmed: each prints its period's reported total revenue."""
    interim = {ref.accession for ref in reports}
    found = []
    for ref in candidates:
        if ref.confirmed or ref.accession in interim or ref.form.upper() != "6-K" or ref.period_end is None:
            continue
        report = next((r for r in reports if r.period_end and abs(r.period_end - ref.period_end) <= _SAME_PERIOD
                       and timedelta(days=-3) <= r.filed - ref.filed <= _REPORT_AFTER), None)
        totals = _totals(report, reports, facts_of) if report else []
        if totals and (text := text_of(ref)) and any(prints_total(text, total) for total in totals):
            found.append(replace(ref, confirmed=True, period_end=report.period_end))
    return found
