from kpi_extractor.ai import Spec
from kpi_extractor.steps import left_out


def _spec(*groups, left=()):
    return Spec.model_validate({"groups": [{"key": key, "label": label, "kind": kind, "kpis": [
        {"key": k, "label": l, "unit": "count"} for k, l in kpis]} for key, label, kind, kpis in groups], "left_out": list(left)})


OPERATING = ("operating", "Operating Metrics", "metric", [("users", "Users"), ("stores", "Stores")])


def test_a_malformed_left_out_item_is_dropped_not_the_reply():
    spec = _spec(OPERATING, left=[{"label": "Gross Margin", "unit": "percent", "reason": "a margin", "group": "operating"},
                                  {"label": "", "reason": "x"}, "CET1", {"label": "Ok", "reason": "y", "unit": "furlongs"}])
    assert [item.label for item in spec.left_out] == ["Gross Margin"]
    spec = _spec(OPERATING, left=[{"label": f"M{i}", "reason": "r"} for i in range(30)])
    assert len(spec.left_out) == 10


def test_propose_lines_take_their_group_when_the_list_has_it():
    spec = _spec(OPERATING, left=[{"label": "YTD Bookings", "unit": "currency", "reason": "year to date only", "group": "operating"},
                                  {"label": "Tier 1 Ratio", "unit": "percent", "reason": "capital ratio"},
                                  {"label": "Gross Margin", "unit": "percent", "reason": "a margin"},  # a rule restated
                                  {"label": "Diluted EPS", "unit": "currency", "reason": "EPS"}])
    lines = left_out.from_propose(spec)
    assert [(line["group"], line["groupLabel"], line["key"], line["source"]) for line in lines] == [
        ("operating", "Operating Metrics", "ytd_bookings", "propose"), (None, None, "tier_1_ratio", "propose")]


def test_dropped_lines_carry_their_group_and_reason():
    before = _spec(OPERATING)
    after = _spec(("operating", "Operating Metrics", "metric", [("users", "Users")]))
    (line,) = left_out.dropped(before, after, left_out.REASON_CAP)
    assert {key: line[key] for key in ("group", "groupLabel", "kind", "key", "label", "reason", "source")} == {
        "group": "operating", "groupLabel": "Operating Metrics", "kind": "metric", "key": "stores", "label": "Stores",
        "reason": "over the list's size cap", "source": "rule"}
    assert left_out.dropped(before, None, "x") == []


def test_merge_keeps_each_line_once_and_none_the_list_holds():
    spec = _spec(OPERATING)
    new = [{"group": "operating", "key": "margin", "label": "Gross Margin", "source": "propose"},
           {"group": None, "key": "users_2", "label": "users", "source": "propose"}]  # listed, by its label
    previous = [{"group": "operating", "key": "stores", "label": "Stores", "source": "owner"},  # added back since
                {"group": "operating", "key": "margin", "label": "Gross margin", "source": "propose"},  # the same line
                {"group": "operating", "key": "nim", "label": "Net Interest Margin", "source": "owner"}, "junk"]
    assert [line["key"] for line in left_out.merge(new, previous, spec)] == ["margin", "nim"]


def test_a_name_another_group_still_lists_is_left_out_all_the_same():
    # Caterpillar: revenue by application's "Power Generation" left out, its retail sales growth's line of that name kept.
    spec = _spec(("growth", "Retail Sales Growth", "metric", [("power_generation", "Power Generation")]))
    new = [{"group": "revenue_by_application", "key": "power_generation", "label": "Power Generation", "source": "rule"},
           {"group": None, "key": "power_generation", "label": "Power Generation", "source": "propose"}]  # no group: held
    assert [(line["group"], line["key"]) for line in left_out.merge(new, [], spec)] == [("revenue_by_application", "power_generation")]


def test_a_label_over_80_characters_is_cut_at_a_word_not_failed():
    long = "Allowance for Credit Losses as a Percent of Portfolio Loans and Leases, Excluding Loans Held for Sale"
    spec = _spec(("operating", "Operating Metrics", "metric", [("acl", long)]))
    assert spec.groups[0].kpis[0].label == "Allowance for Credit Losses as a Percent of Portfolio Loans and Leases"


def test_a_draft_audit_adds_at_most_its_limit_and_never_retires():
    from kpi_extractor import ai
    from kpi_extractor.steps.kpi_list import limit_additions
    answer = ai.Maintenance.model_validate({
        "add_kpis": {"operating": [{"key": f"a{i}", "label": f"A{i}", "unit": "count"} for i in range(5)]},
        "add_groups": [{"key": "aum", "label": "AUM by Asset Class", "kind": "metric",
                        "kpis": [{"key": f"g{i}", "label": f"G{i}", "unit": "currency"} for i in range(10)]}],
        "retire": ["operating.users"]})
    limited = limit_additions(answer, 12)
    assert [k.key for k in limited.add_kpis["operating"]] == [f"a{i}" for i in range(5)]
    assert [k.key for k in limited.add_groups[0].kpis] == [f"g{i}" for i in range(7)]
    assert limit_additions(answer, 3).add_groups == []


def test_a_first_list_is_audited_once_before_it_is_stored(monkeypatch):
    from kpi_extractor import ai
    from tests.pipeline.test_pipeline import FakeControl, _pipeline
    pipeline = _pipeline(FakeControl({}), [])
    pipeline.draft_audit = True
    spec = _spec(OPERATING)
    answer = ai.Maintenance.model_validate({"add_kpis": {"operating": [{"key": "orders", "label": "Orders", "unit": "count"}]},
                                            "retire": ["operating.users"]})
    pipeline._ask = lambda *args, **kwargs: answer
    pipeline._prompt_document = lambda document, spec: "documents"
    audited = pipeline._audit_draft(spec, None)
    assert [k.key for k in audited.groups[0].kpis] == ["users", "stores", "orders"]  # added, none retired
    assert pipeline.result.draft_audit_added == 1 and pipeline.result.draft_before_audit[0]["kpis"][-1]["key"] == "stores"
