"""Every periodic report a company has filed on EDGAR, back to its first: the forms of every era, not only today's.

The 1990s used forms that no longer exist: 10-K405 (an annual report with a box ticked about insider filings) until
2002, 10-KSB and 10-QSB for small businesses until 2009, and 10-KT for a transition period. Amendments are left out:
they restate part of a report, and a later report's comparative columns carry a restatement in full.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

REPORT_FORMS = ("10-K", "10-K405", "10-KT", "10-KSB", "10-Q", "10-QSB", "20-F", "40-F")
_WAITS = (5, 20, 60)  # seconds before each further try after a failed listing


@dataclass
class Report:
    accession: str
    form: str
    filed: date
    filing: object = field(default=None, repr=False)
    period_end: date | None = None  # a release's quarter end (a report's comes from its filing)


def list_reports(company, since: date, predecessors: list = ()) -> list[Report]:
    """The company's periodic reports filed on or after `since`, oldest first, with its predecessors' filed before its
    own first filing (sources/predecessors.py)."""
    reports = []
    for one, before in _registrants(company, predecessors):
        filings = _with_retries(lambda: one.get_filings(form=list(REPORT_FORMS), filing_date=f"{since.isoformat()}:",
                                                        amendments=False))
        reports += [Report(str(filing.accession_no), str(filing.form), filing.filing_date, filing)
                    for filing in filings if str(filing.form) in REPORT_FORMS and (before is None or filing.filing_date < before)]
    return sorted(reports, key=lambda report: report.filed)


def _registrants(company, predecessors: list) -> list[tuple]:
    """The company, then each predecessor with the day its reporting passed on: the next registrant's first filing."""
    if not predecessors:
        return [(company, None)]
    found, after = [(company, None)], company
    for predecessor in predecessors:
        first = min((filing.filing_date for filing in _with_retries(lambda: after.get_filings())), default=None)
        found.append((predecessor, first))
        after = predecessor
    return found


# A results release comes in the weeks after its quarter closes: ASML's about two weeks on, TSMC's management report
# about four, its reviewed quarterly statements about six to eight and its annual statements about twelve. Monthly
# sales and other notices are read too and give nothing; reading them costs little.
RELEASE_WINDOW = (5, 100)  # days after the quarter end
_PAPER = "9999999997-"  # EDGAR's placeholder for a filing made on paper: no documents to read


def list_releases(company, since: date, quarter_end, predecessors: list = ()) -> list[Report]:
    """A foreign filer's 6-Ks furnished within the release window after a quarter end, oldest first, each with that
    quarter's end, its predecessors' among them (list_reports). `quarter_end(day)` is the latest quarter end at least
    five days before a day."""
    found = []
    for one, before in _registrants(company, predecessors):
        found += _releases(one, since, quarter_end, before)
    return sorted(found, key=lambda report: report.filed)


def _releases(company, since: date, quarter_end, before) -> list[Report]:
    filings = _with_retries(lambda: company.get_filings(form="6-K", filing_date=f"{since.isoformat()}:"))
    found = []
    for filing in filings:
        if str(filing.form) != "6-K" or str(filing.accession_no).startswith(_PAPER) or before and filing.filing_date >= before:
            continue
        end = quarter_end(filing.filing_date)
        if RELEASE_WINDOW[0] <= (filing.filing_date - end).days <= RELEASE_WINDOW[1]:
            found.append(Report(str(filing.accession_no), "6-K", filing.filing_date, filing, period_end=end))
    return sorted(found, key=lambda report: report.filed)


def _with_retries(read):
    for wait in (*_WAITS, None):
        try:
            return read()
        except Exception:  # noqa: BLE001 - EDGAR's listing fails now and then; the last failure is raised
            if wait is None:
                raise
            time.sleep(wait)
    return None


def filing_for(accession: str):
    """A filing again by its accession, for a second look at its documents."""
    from edgar import find
    return _with_retries(lambda: find(accession))
