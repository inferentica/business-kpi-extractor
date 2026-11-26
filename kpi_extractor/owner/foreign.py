"""A foreign filer's 6-Ks for an owner-built company, told apart by code, never by the AI.

Arm files each quarter's financial statements on a 6-K of their own, with full XBRL, and its results on a second 6-K
the same day (a shareholder letter behind a one-page Exhibit 99.1). The listing marks every 6-K a candidate for the AI
to classify; an owner-built company takes no AI, so these two rules stand in for it:

- an interim report is a 6-K with XBRL whose report date is a quarter end before its filing date (other 6-Ks, such as
  AGM results or appointments, carry their filing date as the report date);
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
