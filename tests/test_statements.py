from datetime import date

import pandas as pd

from kpi_extractor.statements import statement_lines


class _Statement:
    def __init__(self, frame):
        self.frame = frame

    def to_dataframe(self):
        return self.frame


class _Statements:
    def __init__(self, frames):
        self.frames = frames

    def __getattr__(self, name):
        if name not in self.frames:
            raise AttributeError(name)
        return lambda: _Statement(self.frames[name])


class _Xbrl:
    def __init__(self, **frames):
        self.statements = _Statements(frames)


def _income():
    return pd.DataFrame([
        {"concept": "Revenue", "label": "Net sales", "2026-06-27 (Q3)": 100.0, "2025-06-28 (Q3)": 90.0,
         "2026-06-27 (YTD)": 300.0, "level": 4, "abstract": False, "dimension": False},
        {"concept": "Revenue", "label": "iPhone", "2026-06-27 (Q3)": 50.0, "2025-06-28 (Q3)": 45.0,
         "2026-06-27 (YTD)": 150.0, "level": 4, "abstract": False, "dimension": True},
        {"concept": "OpexAbstract", "label": "Operating expenses:", "level": 3, "abstract": True, "dimension": False},
        {"concept": "RnD", "label": "Research", "2026-06-27 (Q3)": 10.0, "2026-06-27 (YTD)": 30.0, "level": 5,
         "abstract": False, "dimension": False},
        {"concept": "EpsAbstract", "label": "Earnings per share:", "level": 3, "abstract": True, "dimension": False},
    ])


def test_own_period_lines_in_order_without_dimensions():
    lines = statement_lines(_Xbrl(income_statement=_income()), date(2026, 6, 27), annual=False)
    quarter = [line for line in lines if line["duration"] == "quarter"]
    assert [(line["label"], line["value"], line["level"]) for line in quarter] == [
        ("Net sales", 100.0, 1), ("Operating expenses:", None, 0), ("Research", 10.0, 2)]
    assert [line["line"] for line in quarter] == [0, 1, 2]
    assert [line["value"] for line in lines if line["duration"] == "ytd" and not line["is_heading"]] == [300.0, 30.0]


def test_annual_report_keeps_only_the_year():
    frame = _income().rename(columns={"2026-06-27 (Q3)": "2026-06-27 (FY)"})
    lines = statement_lines(_Xbrl(income_statement=frame), date(2026, 6, 27), annual=True)
    assert {line["duration"] for line in lines} == {"year"}


def test_missing_statement_is_skipped():
    assert statement_lines(_Xbrl(), date(2026, 6, 27), annual=False) == []
