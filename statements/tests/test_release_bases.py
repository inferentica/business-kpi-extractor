from datetime import date
from types import SimpleNamespace

from statement_extractor.pipeline.company import _release_bases


def _reading(year, form, basis, source="html", period="Q4", own=True, end=None, filed=None, accession=None):
    return SimpleNamespace(fiscal_year=year, form=form, basis=basis, source=source, fiscal_period=period, own=own,
                           period_end=end or date(int(year), 12, 31), filed=filed or date(int(year) + 1, 2, 1),
                           accession=accession or f"{form}-{year}-{period}-{basis}")


def test_a_release_takes_its_quarters_own_report_basis_before_the_annual_reports():
    # Sabesp's Q1 2010 release names no basis; its Q1 2010 quarterly information states Brazilian GAAP, and the FY2010
    # 20-F restated 2010 on IFRS (round 84 read the release on IFRS). Its comparative takes the release's basis.
    readings = [_reading("2010", "20-F", "IFRS", period="FY"),
                _reading("2010", "6-K", "Brazilian GAAP", period="Q1", end=date(2010, 3, 31), filed=date(2010, 7, 14), accession="itr"),
                _reading("2010", "6-K", None, period="Q1", end=date(2010, 3, 31), filed=date(2010, 5, 20), accession="release"),
                _reading("2009", "6-K", None, period="Q1", own=False, end=date(2009, 3, 31), filed=date(2010, 5, 20), accession="release"),
                _reading("2010", "6-K", None, period="Q2", end=date(2010, 6, 30), filed=date(2010, 8, 10), accession="q2")]
    _release_bases(readings)
    assert [reading.basis for reading in readings[2:]] == ["Brazilian GAAP", "Brazilian GAAP", "IFRS"]


def test_a_release_stating_no_basis_takes_its_years_home_regulator_basis_before_the_nearest_years():
    # SQM's 2012 release: its 2010 quarter read "Chilean GAAP" from the 2009 20-F, though CMF holds 2010 on IFRS.
    readings = [_reading("2009", "20-F", "Chilean GAAP", period="FY"),
                _reading("2010", "CMF-FY", "IFRS", source="cmf", period="FY"),
                _reading("2010", "6-K", None), _reading("2008", "6-K", None)]
    _release_bases(readings)
    assert [reading.basis for reading in readings[2:]] == ["IFRS", "Chilean GAAP"]
