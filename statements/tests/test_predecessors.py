from datetime import date
from types import SimpleNamespace

from statement_extractor.sources.listing import list_releases, list_reports


class _Company:
    def __init__(self, filings):
        self.filings = [SimpleNamespace(accession_no=accession, form=form, filing_date=filed) for accession, form, filed in filings]

    def get_filings(self, form=None, filing_date=None, amendments=True):
        forms = [form] if isinstance(form, str) else form
        return [filing for filing in self.filings if forms is None or filing.form in forms]


def test_a_predecessors_filings_count_until_the_company_files_its_own():
    # Embraer S.A. (from April 2006) and Embraer - Empresa Brasileira de Aeronautica before it.
    embraer = _Company([("0001275287-06-000001", "6-K", date(2006, 4, 3)), ("0001292814-06-000002", "20-F", date(2006, 6, 30))])
    before = _Company([("0000950123-03-000003", "20-F", date(2003, 6, 30)), ("0000947871-03-000004", "6-K", date(2003, 5, 2)),
                       ("0000947871-06-000005", "6-K", date(2006, 2, 1)), ("0000947871-08-000006", "6-K", date(2008, 5, 2))])
    assert [report.accession for report in list_reports(embraer, date(1993, 1, 1), [before])] == [
        "0000950123-03-000003", "0001292814-06-000002"]
    from statement_extractor.domain.calendar import quarter_end_before
    releases = [report.accession for report in list_releases(embraer, date(1993, 1, 1),
                                                             lambda day: quarter_end_before(day, "1231"), [before])]
    assert "0000947871-08-000006" not in releases and "0000947871-03-000004" in releases  # nothing after the company's first
    assert [report.accession for report in list_reports(embraer, date(1993, 1, 1))] == ["0001292814-06-000002"]
