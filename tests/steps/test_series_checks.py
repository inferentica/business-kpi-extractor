"""steps.series_checks, step by step, on stored figures alone (nothing is read again)."""
from types import SimpleNamespace

from kpi_extractor.pipeline import SymbolPipeline


class Control:
    def call(self, operation, **payload):
        return {}


def run(values):
    pipeline = SymbolPipeline(Control(), "TSM", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="TSMC", fiscal_year_end="1231", foreign=True)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    return pipeline


def value(kpi, amount, *, period="FY", year="2024", end="2024-12-31", status="needs_review", notes=(), unit="currency",
          kind="revenue_breakdown", group="kpi_nodes", filing="F1", locator=None):
    return {"group_key": group, "kpi_key": kpi, "kpi_label": kpi, "group_label": group, "group_kind": kind, "unit": unit,
            "method": "ai", "validation_status": status, "notes": list(notes), "value": amount, "fiscal_year": year,
            "fiscal_period": period, "period_end": end, "source_accession": filing, "locator": dict(locator or {})}


def test_a_full_year_held_for_a_jump_stands_when_its_years_breakdown_reconciles():
    # TSMC's FY2024 nodes: 3nm ramped 3.5x in a year, and the year's table adds up to its total exactly.
    total = {"total": 100.0}
    rows = [value("n3", 60.0, notes=["changed 3.5x in a quarter"], locator=total),
            value("n5", 40.0, status="verified", locator=total)]
    [cleared] = run(rows)._recheck_flagged()
    assert (cleared["kpi_key"], cleared["validation_status"], cleared["notes"]) == ("n3", "verified", ["the year's breakdown reconciles"])


def test_a_full_year_held_for_more_than_a_jump_keeps_its_hold():
    rows = [value("n3", 60.0, notes=["changed 3.5x in a quarter", "scale not stated in the document"], locator={"total": 100.0}),
            value("n5", 40.0, status="verified", locator={"total": 100.0})]
    assert run(rows)._recheck_flagged() == []


def test_an_immaterial_row_held_for_a_jump_stands_on_its_total():
    rows = [value("n7", 0.5, period="Q3", end="2024-09-30", notes=["changed 4.0x in a quarter"]),
            value("n5", 99.5, period="Q3", end="2024-09-30", status="verified")]
    [cleared] = run(rows)._recheck_flagged()
    assert cleared["notes"] == ["immaterial row; the total checks it"]


def test_a_small_counts_old_ratio_flag_is_cleared_by_todays_small_count_rule():
    # ASML's used systems sold, 10 then 3: flagged by ratio before small counts moved by units.
    before = value("used", 10.0, period="Q2", end="2024-06-30", status="verified", unit="count", kind="metric", group="kpi_systems")
    now = value("used", 3.0, period="Q3", end="2024-09-30", unit="count", kind="metric", group="kpi_systems",
                notes=["changed 0.3x in a quarter", "confirmed by review"])
    [cleared] = run([before, now])._outdated_flags()
    assert cleared["validation_status"] == "verified" and cleared["notes"][-1] == "a small count's move, within the rule for small counts"
    # A check's own mark is not set aside here: the figure keeps its hold.
    settled = {**now, "notes": ["changed 0.3x in a quarter", "settled by the checks"]}
    assert run([before, settled])._outdated_flags() == []


def test_parts_flagged_off_their_total_are_cleared_once_they_add_up_to_the_releases_total():
    stated = {"total": 100.0}
    rows = [value("a", 60.0, period="Q3", end="2024-09-30", notes=["parts differ from the total by 12.0%"], locator=stated),
            value("b", 40.0, period="Q3", end="2024-09-30", notes=["parts differ from the total by 12.0%"], locator=stated)]
    cleared = run(rows)._outdated_flags()
    assert sorted(v["kpi_key"] for v in cleared) == ["a", "b"]
    assert {v["notes"][-1] for v in cleared} == {"the parts add up to the release's total"}
