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
    assert len(spec.left_out) == 15


def test_propose_lines_take_their_group_when_the_list_has_it():
    spec = _spec(OPERATING, left=[{"label": "Gross Margin", "unit": "percent", "reason": "a margin", "group": "operating"},
                                  {"label": "Tier 1 Ratio", "unit": "percent", "reason": "capital ratio"}])
    lines = left_out.from_propose(spec)
    assert [(line["group"], line["groupLabel"], line["key"], line["source"]) for line in lines] == [
        ("operating", "Operating Metrics", "gross_margin", "propose"), (None, None, "tier_1_ratio", "propose")]


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


def test_a_label_over_80_characters_is_cut_at_a_word_not_failed():
    long = "Allowance for Credit Losses as a Percent of Portfolio Loans and Leases, Excluding Loans Held for Sale"
    spec = _spec(("operating", "Operating Metrics", "metric", [("acl", long)]))
    assert spec.groups[0].kpis[0].label == "Allowance for Credit Losses as a Percent of Portfolio Loans and Leases"
