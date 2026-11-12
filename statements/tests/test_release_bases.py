from types import SimpleNamespace

from statement_extractor.pipeline.company import _release_bases


def _reading(year, form, basis, source="html", period="Q4", own=True):
    return SimpleNamespace(fiscal_year=year, form=form, basis=basis, source=source, fiscal_period=period, own=own)


def test_a_release_stating_no_basis_takes_its_years_home_regulator_basis_before_the_nearest_years():
    # SQM's 2012 release: its 2010 quarter read "Chilean GAAP" from the 2009 20-F, though CMF holds 2010 on IFRS.
    readings = [_reading("2009", "20-F", "Chilean GAAP", period="FY"),
                _reading("2010", "CMF-FY", "IFRS", source="cmf", period="FY"),
                _reading("2010", "6-K", None), _reading("2008", "6-K", None)]
    _release_bases(readings)
    assert [reading.basis for reading in readings[2:]] == ["IFRS", "Chilean GAAP"]
