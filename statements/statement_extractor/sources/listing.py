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


def list_reports(company, since: date) -> list[Report]:
    """The company's periodic reports filed on or after `since`, oldest first."""
    filings = _with_retries(lambda: company.get_filings(form=list(REPORT_FORMS), filing_date=f"{since.isoformat()}:",
                                                        amendments=False))
    reports = [Report(str(filing.accession_no), str(filing.form), filing.filing_date, filing)
               for filing in filings if str(filing.form) in REPORT_FORMS]
    return sorted(reports, key=lambda report: report.filed)


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
