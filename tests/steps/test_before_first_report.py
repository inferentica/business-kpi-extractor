"""A balance KPI at the dates a spin-off's first reports show beside their own."""
from datetime import date
from types import SimpleNamespace

import pandas as pd

from kpi_extractor.readers.tagged_metrics import Tag
from kpi_extractor.sources.base import FilingRef
from kpi_extractor.steps.shared import RecordBuilding
from kpi_extractor.steps.tagged_kpis import TaggedKpiReading

RPO = "us-gaap:RevenueRemainingPerformanceObligation"


class _Reader(TaggedKpiReading, RecordBuilding):
    def __init__(self, frames):
        self.symbol, self.offset, self.logged = "GEV", 0, []
        self.profile = SimpleNamespace(fiscal_year_end="1231")
        self._frames = frames

    def _xbrl(self, ref):
        return SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: self._frames[ref.accession]))

    def log(self, message):
        self.logged.append(message)


def _instants(values):
    return pd.DataFrame([{"concept": RPO, "period_type": "instant", "period_instant": day, "period_start": None, "period_end": day, "numeric_value": value}
                         for day, value in values.items()])


def test_a_spin_offs_first_reports_give_its_backlog_before_it_filed():
    # GE Vernova's first 10-Q (Q1 2024) gives RPO at March 31, 2024 and at December 31 and March 31, 2023; its next
    # two give June 30 and September 30, 2023. Each earlier date fills the quarter it ends.
    frames = {"q1": _instants({"2024-03-31": 116_293.0, "2023-12-31": 115_598.0, "2023-03-31": 108_221.0}),
              "q2": _instants({"2024-06-30": 115_476.0, "2023-12-31": 115_598.0, "2023-06-30": 114_062.0}),
              "q3": _instants({"2024-09-30": 117_746.0, "2023-12-31": 115_598.0, "2023-09-30": 112_187.0})}
    quarters = {("2024", f"Q{i}"): (FilingRef(name, "10-Q", date(2024, 1 + 3 * i, 1), "periodic_report", f"https://x/{name}"), end)
                for i, (name, end) in enumerate((("q1", date(2024, 3, 31)), ("q2", date(2024, 6, 30)), ("q3", date(2024, 9, 30))), 1)}
    template = {"group_label": "Operating Metrics", "group_kind": "metric", "group_order": 20, "kpi_label": "Total Backlog",
                "kpi_order": 1, "unit": "currency", "currency": "USD"}
    reader, tag, kpi = _Reader(frames), Tag(RPO, (), True), SimpleNamespace(key="total_backlog")
    out = reader._before_first_report(tag, kpi, "kpi_operating", template, quarters, set(quarters), False)
    assert {(r["fiscal_year"], r["fiscal_period"]): r["value"] for r in out} == {
        ("2023", "Q1"): 108_221.0, ("2023", "Q4"): 115_598.0, ("2023", "Q2"): 114_062.0, ("2023", "Q3"): 112_187.0}
    assert next(r for r in out if r["fiscal_period"] == "Q4")["source_accession"] == "q1"  # the first report that gives it
    # A company with ten years of reports never reaches past them.
    many = {(str(2016 + i // 4), f"Q{i % 4 + 1}"): quarters[("2024", "Q1")] for i in range(40)}
    assert reader._before_first_report(tag, kpi, "kpi_operating", template, many, set(many), False) == []
