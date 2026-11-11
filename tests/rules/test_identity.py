

def test_a_row_naming_only_the_side_its_sign_falls_on_is_the_listed_line_naming_both():
    # JPMorgan's "Investment securities losses" (quarters with a loss) is its listed "Investment Securities Gains/(Losses)":
    # under its own key it looked like a new line, and the listed one missing, so no replay of the table stood.
    from kpi_extractor.ai import GroupSpec
    from kpi_extractor.rules.identity import _listed_kpi
    group = GroupSpec.model_validate({"key": "revenue", "label": "Revenue by Product", "kind": "revenue_breakdown", "kpis": [
        {"key": "securities", "label": "Investment Securities Gains/(Losses)", "unit": "currency"},
        {"key": "other", "label": "Other Income", "unit": "currency"}]})
    assert _listed_kpi(group, "Investment securities losses").key == "securities"
    assert _listed_kpi(group, "Investment securities gains").key == "securities"
    assert _listed_kpi(group, "Investment securities gains/(losses)").key == "securities"
    assert _listed_kpi(group, "Other losses") is None  # only a listed label that names both sides
