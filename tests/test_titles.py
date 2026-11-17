from kpi_extractor.ai import Spec
from kpi_extractor.rules.titles import breakdown_names, keep_titles, names_segment
from kpi_extractor.steps.kpi_list import keep_current_groups

BREAKDOWNS = {
    "products": {"label": "Revenue by Product", "members": {"createsolutions": "Create Solutions", "growsolutions": "Grow Solutions"}},
    "products_levels": {"label": "Revenue by Product subtotals", "members": {"googleservices": "Google Services"}},
    "x_markets_of_customers": {"label": "Revenue by Markets of Customers", "members": {"hpc": "High Performance Computing", "iot": "IoT"}},
}


def test_a_standard_axis_keeps_its_title_and_takes_row_names():
    names = breakdown_names({"products": {"label": "Revenue by Segment", "members": {"growsolutions": "Grow"}}}, BREAKDOWNS)
    assert names == {"products": {"members": {"growsolutions": "Grow"}}}


def test_subtotals_never_take_the_breakdowns_own_title():
    # Alphabet: the AI titled its product subtotals "Revenue by Product", the product breakdown's own title.
    assert breakdown_names({"products_levels": {"label": "Revenue by Product"}}, BREAKDOWNS) == {}


def test_an_own_axis_is_named_unless_another_breakdown_has_the_title():
    assert breakdown_names({"x_markets_of_customers": {"label": "Revenue by Platform"}}, BREAKDOWNS) == {
        "x_markets_of_customers": {"label": "Revenue by Platform", "members": {}}}
    assert breakdown_names({"x_markets_of_customers": {"label": "Revenue by product"}}, BREAKDOWNS) == {}


def test_stored_names_stay():
    stored = {"products": {"label": "Revenue by Segment", "members": {"growsolutions": "Grow Solutions"}}}
    proposed = {"products": {"label": "Revenue by Business", "members": {"growsolutions": "Grow", "createsolutions": "Create"}}}
    assert breakdown_names(proposed, BREAKDOWNS, stored) == {
        "products": {"label": "Revenue by Segment", "members": {"growsolutions": "Grow Solutions", "createsolutions": "Create"}}}


def test_two_rows_never_read_alike():
    names = breakdown_names({"products": {"members": {"createsolutions": "Grow Solutions"}}}, BREAKDOWNS)
    assert names == {}


def test_unknown_breakdowns_and_members_are_ignored():
    assert breakdown_names({"segments": {"label": "Revenue by Segment"}, "products": {"members": {"other": "Other"}}}, BREAKDOWNS) == {}


def test_a_spec_reads_names_without_a_title():
    spec = Spec.model_validate({"groups": [], "names": {"products": {"members": {"growsolutions": "Grow"}}}})
    assert spec.names["products"].label is None
    assert breakdown_names(spec.names, BREAKDOWNS) == {"products": {"members": {"growsolutions": "Grow"}}}


def _group(key, label):
    return {"key": key, "label": label, "kind": "metric", "kpis": [{"key": "users", "label": "Users", "unit": "count"}]}


def test_a_list_proposed_again_keeps_each_tracked_groups_title():
    assert keep_titles([_group("operating", "Key Metrics"), _group("new", "New")], [_group("operating", "Operating Metrics")]) == [
        _group("operating", "Operating Metrics"), _group("new", "New")]
    current = Spec.model_validate({"groups": [_group("operating", "Operating Metrics")]})
    proposed = Spec.model_validate({"groups": [_group("operating", "Key Metrics")]})
    assert [group.label for group in keep_current_groups(proposed, current).groups] == ["Operating Metrics"]
    same = Spec.model_validate({"groups": [_group("operating", "Operating Metrics")]})
    assert keep_current_groups(same, current) is same


def test_a_title_names_its_segment_in_full_or_by_initials():
    assert names_segment("CCB Revenue by Business", "Consumer & Community Banking")
    assert names_segment("AWM Revenue by Business", "Asset & Wealth Management")
    assert names_segment("Asset & Wealth Management Revenue by Product", "Asset & Wealth Management")
    assert not names_segment("Revenue by Product", "Asset & Wealth Management")
    assert not names_segment("Revenue by Business", "Corporate")
