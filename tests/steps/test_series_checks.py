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


def test_a_full_year_held_for_more_than_a_jump_keeps_its_hold():
    rows = [value("n3", 60.0, notes=["changed 3.5x in a quarter", "scale not stated in the document"], locator={"total": 100.0}),
            value("n5", 40.0, status="verified", locator={"total": 100.0})]
    assert run(rows)._recheck_flagged() == []


def test_parts_flagged_off_their_total_are_cleared_once_they_add_up_to_the_releases_total():
    stated = {"total": 100.0}
    rows = [value("a", 60.0, period="Q3", end="2024-09-30", notes=["parts differ from the total by 12.0%"], locator=stated),
            value("b", 40.0, period="Q3", end="2024-09-30", notes=["parts differ from the total by 12.0%"], locator=stated)]
    cleared = run(rows)._outdated_flags()
    assert sorted(v["kpi_key"] for v in cleared) == ["a", "b"]
    assert {v["notes"][-1] for v in cleared} == {"the parts add up to the release's total"}


def test_a_breakdown_with_a_line_the_owner_confirmed_clears_once_its_parts_add_up():
    # JPMorgan's Q4 2020 client segments: the owner rejected a "Commercial Banking" copy of Middle Market Banking and
    # confirmed the line as Commercial & Specialized Industries; the rest were held off the total the copy made.
    stated, off = {"total": 2463.0}, "parts differ from the total by -25.6%"
    q4 = dict(period="Q4", end="2020-12-31")
    rows = [value("csi", 947.0, status="verified", **q4, notes=["confirmed by the owner"], locator=stated),
            value("ccb", 856.0, **q4, notes=[off], locator=stated),
            value("cre", 630.0, **q4, notes=[off, "lines differ from Commercial Banking by -33.5%"], locator=stated),
            value("other", 30.0, **q4, notes=[off], locator=stated),
            value("Commercial Banking", 947.0, status="rejected", **q4, locator=stated)]
    rows[0]["owner_decision"], rows[4]["owner_decision"] = "confirmed", "rejected"
    cleared = run(rows)._outdated_flags()
    assert sorted(v["kpi_key"] for v in cleared) == ["ccb", "cre", "other"]
    assert [v["notes"] for v in cleared if v["kpi_key"] == "cre"] == [[off, "the parts add up to the release's total"]]
    # A parent still served keeps its lines' hold, and so does a line no one confirmed.
    rows[4]["validation_status"], rows[4]["owner_decision"] = "verified", None
    assert run(rows)._outdated_flags() == []
    rows[4]["validation_status"], rows[0]["owner_decision"] = "rejected", None
    assert run(rows)._outdated_flags() == []


def test_a_figure_equal_only_to_another_xbrl_line_is_no_proof():
    from kpi_extractor.steps.shared import equals_named_line, xbrl_figures
    xbrl = [{"group_key": "segments", "kpi_key": "reconciling", "kpi_label": "Reconciling Items", "method": "xbrl",
             "validation_status": "verified", "fiscal_period": "Q4", "period_end": "2024-12-31", "value": -448e6, "locator": {}},
            {"group_key": "products", "kpi_key": "services", "kpi_label": "Services", "method": "xbrl",
             "validation_status": "verified", "fiscal_period": "Q4", "period_end": "2024-12-31", "value": 26.3e9, "locator": {}}]
    official = xbrl_figures(xbrl)
    loss = {"kpi_label": "Net Gains (Losses) from Debt Securities", "fiscal_period": "Q4", "period_end": "2024-12-31", "value": -448e6}
    services = {"kpi_label": "Services", "fiscal_period": "Q4", "period_end": "2024-12-31", "value": 26.3e9}
    assert not equals_named_line(official, loss) and equals_named_line(official, services)
    # A flagged figure is no longer verified on that coincidence.
    wells = value("debt_securities", -448e6, period="Q4", kind="metric", group="kpi_income", notes=["changed 9.0x in a quarter"])
    pipeline = run([wells, *({**x, "fiscal_year": "2024"} for x in xbrl)])
    assert [v["kpi_key"] for v in pipeline._prove_flagged([])] == []


def test_served_figures_that_stood_on_an_ended_clear_are_judged_again_by_their_filings():
    stated = {"quote": "New capital raised was $58.9 billion", "value_text": "$58.9 billion"}
    explained_stated = value("raised", 58.9e9, period="Q2", kind="metric", group="kpi_ops", status="verified", locator=stated,
                             notes=["change explained: a record quarter for fundraising"])
    explained_only = value("aus", 1.406e12, period="Q2", kind="metric", group="kpi_ops", status="verified",
                           locator={"table": "T1", "row": 3, "col": 1}, notes=["change explained: market appreciation"])
    unnamed = value("debt_securities", -448e6, period="Q4", kind="metric", group="kpi_income", status="verified",
                    notes=["equals a figure reported in XBRL"])
    owners = {**explained_only, "kpi_key": "aus_owner", "owner_decision": "confirmed"}
    changed = {v["kpi_key"]: v for v in run([explained_stated, explained_only, unnamed, owners])._withdraw_unproven_clears()}
    assert changed["raised"]["validation_status"] == "verified" and changed["raised"]["notes"][-1] == "stated in its filing"
    assert changed["aus"]["validation_status"] == "needs_review"
    assert changed["debt_securities"]["validation_status"] == "needs_review"
    assert "equals a figure reported in XBRL" not in changed["debt_securities"]["notes"]
    assert "aus_owner" not in changed  # what the owner decided stays


def test_a_figure_held_only_for_how_far_it_moved_or_its_size_is_cleared():
    # Since 2026-11-16 no check holds a figure for a jump or its size; the figures such checks held are cleared from the
    # stored notes alone: TSMC's 3nm ramping 3.5x in a year, an immaterial row's swing, ASML's used systems 10 to 3
    # (kept beside its review mark), an amount far from its other periods, a breakdown taken for part of its table.
    from kpi_extractor.quality.reasons import SIZE_HOLD_LIFTED
    rows = [value("n3", 60.0, notes=["changed 3.5x in a quarter"]), value("n5", 40.0, status="verified"),
            value("n7", 0.5, period="Q3", end="2024-09-30", notes=["changed 4.0x in a quarter"]),
            value("used", 3.0, period="Q3", end="2024-09-30", unit="count", kind="metric", group="kpi_systems",
                  notes=["changed 0.3x in a quarter", "confirmed by review"]),
            value("fees", 3477.0, period="Q3", end="2024-09-30", kind="metric", group="kpi_fees",
                  notes=["amount far from its other periods or from revenue: a scale not stated"]),
            *(value(k, v, period="Q1", end="2016-03-31", group="kpi_awm", filing="G1",
                    notes=["fewer rows and under half the total of the quarter compared: likely part of the table", *extra])
              for k, v, extra in (("management", 1165.0, ["equals a figure reported in XBRL"]), ("incentive", 46.0, [])))]
    cleared = {v["kpi_key"]: v for v in run(rows)._outdated_flags()}
    assert set(cleared) == {"n3", "n7", "used", "fees", "management", "incentive"}
    assert all(v["validation_status"] == "verified" and v["notes"][-1] == SIZE_HOLD_LIFTED for v in cleared.values())
    assert cleared["used"]["notes"] == ["confirmed by review", SIZE_HOLD_LIFTED]
    assert cleared["management"]["notes"] == ["equals a figure reported in XBRL", SIZE_HOLD_LIFTED]


def test_a_figure_held_for_anything_besides_its_size_keeps_its_hold():
    # A check's own mark ("settled by the checks"), another hold, an owner's decision, or a line of the same breakdown
    # held for its parts: the jump's hold goes only with every hold of the group.
    settled = value("used", 3.0, period="Q3", unit="count", kind="metric", group="kpi_systems",
                    notes=["changed 0.3x in a quarter", "settled by the checks"])
    shared = value("dap", 9e9, period="Q3", kind="metric", group="kpi_ops",
                   notes=["changed 9.0x in a quarter", "read from the same table cell as another listed metric"])
    decided = {**value("mau", 9e9, period="Q3", kind="metric", group="kpi_ops", notes=["changed 9.0x in a quarter"]),
               "owner_decision": "reject"}
    group = [value("a", 60.0, period="Q2", end="2024-06-30", notes=["changed 3.5x in a quarter"]),
             value("b", 30.0, period="Q2", end="2024-06-30", notes=["parts differ from the total by 5.0%"])]
    assert run([settled, shared, decided, *group])._outdated_flags() == []
