"""Fresh lists scored one to one, on the lines the company still reports (p4-listscore)."""
from kpi_extractor.evals.evaluate import score
from kpi_extractor.evals.list_score import current_lines, match_one_to_one, proposed_again, score_lists


def spec(*groups):
    return {"groups": [{"key": key, "label": key, "kpis": [{"key": f"k{i}", "label": label} for i, label in enumerate(labels)]}
                       for key, labels in groups]}


def test_one_fresh_line_names_at_most_one_owner_line():
    # JPMorgan's 21 fresh lines once "named" 41 of the owner's 52: "Net Revenue" agreed with every "... Net Revenue".
    owner = ["Net Revenue", "Markets Net Revenue", "Payments Net Revenue"]
    assert match_one_to_one(owner, ["Net Revenue"]) == {0: 0}
    assert match_one_to_one(owner, ["Payments Net Revenue", "Net Revenue (in millions)"]) == {0: 1, 2: 0}


def test_recall_counts_only_lines_the_company_still_reports_and_lines_served_from_xbrl():
    owner = {"X": {**spec(("ops", ["Cloud", "Devices", "Facebook DAUs"]), ("seg", ["Grow Solutions"])), "values": [
        {"group_key": "kpi_ops", "kpi_key": "k0", "period_end": "2026-09-30", "validation_status": "verified"},
        {"group_key": "kpi_ops", "kpi_key": "k1", "period_end": "2026-09-30", "validation_status": "verified"},
        {"group_key": "kpi_ops", "kpi_key": "k2", "period_end": "2023-12-31", "validation_status": "verified"},  # history
        {"group_key": "kpi_seg", "kpi_key": "k0", "period_end": "2026-09-30", "validation_status": "verified"}]}}
    fresh = {"X": spec(("ops", ["Cloud", "Operating income"]))}
    captured = {"X": [{"kpi_label": "Grow Solutions", "method": "xbrl", "validation_status": "verified"}]}
    assert current_lines(owner["X"], owner["X"]["values"]) == {("ops", "k0"), ("ops", "k1"), ("seg", "k0")}
    lists = score_lists(fresh, owner, captured)
    row = lists["byCompany"]["X"]
    assert (row["current"], row["history"], row["named"], row["fromXbrl"]) == (3, 1, 1, 1)
    assert lists["recall"] == 2 / 3 and lists["precision"] == 0.5 and row["notNamed"] == ["Devices"]


def test_a_breakdown_the_owner_kept_under_another_title_is_not_proposed_again():
    # UnitedHealth: the fresh "Revenue by Business Line" is the owner's kept "Revenue by Customer Type".
    removed = [{"symbol": "UNH", "label": "Revenue by Segment", "kpis": ["UnitedHealthcare", "Optum"]}]
    lines = ["UnitedHealthcare Employer & Individual", "UnitedHealthcare Medicare & Retirement", "Optum Health"]
    owner = {"UNH": {"groups": [{"label": "Revenue by Customer Type", "kpis": [{"label": label} for label in lines]}]}}
    fresh = {"UNH": {"groups": [{"label": "Revenue by Business Line", "kpis": [{"label": label} for label in lines]}]}}
    assert proposed_again(fresh, owner, removed) == []


def test_a_golden_figure_under_another_group_title_counts_when_its_group_holds_every_golden_line():
    golden = [{"symbol": "MS", "period_end": "2026-09-30", "label": "^Advisory$", "group": "^Investment Banking Revenue by Product$",
               "unit": "currency", "value": 887e6},
              {"symbol": "MS", "period_end": "2026-09-30", "label": "^Fixed income underwriting$",
               "group": "^Investment Banking Revenue by Product$", "unit": "currency", "value": 747e6}]
    row = {"group_key": "kpi_ib", "group_label": "Revenue by Product", "fiscal_year": "2026", "fiscal_period": "Q3",
           "period_end": "2026-09-30", "unit": "currency", "validation_status": "verified", "currency": "USD", "method": "ai"}
    captured = {"MS": [{**row, "kpi_key": "adv", "kpi_label": "Advisory", "value": 887e6},
                       {**row, "kpi_key": "fiu", "kpi_label": "Fixed income underwriting", "value": 747e6}]}
    report = score(golden, captured)
    assert report["correct"] == 2 and report["retitled"] == 2
    # A lone line under another title proves nothing about its group.
    lone = score(golden, {"MS": [captured["MS"][0]]})
    assert lone["correct"] == 0 and lone["missing"] == 2


def test_a_list_draft_runs_the_pipeline_lists_only(monkeypatch):
    from kpi_extractor.evals import lists_eval
    from kpi_extractor.pipeline import SymbolResult
    seen = {}

    class Pipeline:
        def __init__(self, control, symbol, quarters, force, log=None, lists_only=False):
            seen.update(symbol=symbol, force=force, lists_only=lists_only)
            control.specs[symbol] = {"groups": []}

        def run(self):
            return SymbolResult("X")
    monkeypatch.setattr(lists_eval, "SymbolPipeline", Pipeline)
    _result, spec, values = lists_eval.draft_list(object(), "X", 4)
    assert seen == {"symbol": "X", "force": True, "lists_only": True} and spec == {"groups": []} and values == []


def test_the_lists_summary_line_names_both_measures():
    from kpi_extractor.evals.lists_eval import _line
    scored = {"companies": 2, "recall": 0.6, "current": 10, "named": 5, "fromXbrl": 1, "history": 2, "precision": 0.9,
              "kept": 9, "fresh": 10}
    assert _line("Held out", scored) == ("Held out: 2 companies, recall 60.0% of 10 lines still reported (5 named, 1 from XBRL; "
                                         "2 history apart), precision 90.0% (9/10)")
    assert _line("Held out", {"companies": 0}) == "Held out: no companies"


def test_nanometer_nodes_match_however_written():
    from kpi_extractor.evals.list_score import match_one_to_one
    assert match_one_to_one(["7nm", "16nm", "2nm"], ["2-nanometer", "7 nanometer", "16-nanometer"]) == {0: 1, 1: 2, 2: 0}
