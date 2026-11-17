"""quality.metric_totals: totals inside a metric group, learned from the group's own quarters."""
from types import SimpleNamespace

from kpi_extractor.ai import GroupSpec
from kpi_extractor.quality.metric_totals import adds_up, total_runs, unbalanced_quarters
from kpi_extractor.steps.replay_reading import ReplayReading

LINES = [("ci", "Commercial and Industrial"), ("cre", "Commercial Mortgage"), ("total_commercial", "Total Commercial Loans"),
         ("he", "Home Equity Loans"), ("cards", "Credit Cards"), ("total_consumer", "Total Consumer Loans"),
         ("total", "Total Loans")]
RIGHT = {"ci": 60565e6, "cre": 16348e6, "total_commercial": 76913e6, "he": 7529e6, "cards": 930e6,
         "total_consumer": 8459e6, "total": 85372e6}
# KeyCorp's Q1/Q2 shape: the consumer lines and both totals from the nonperforming assets table.
NONPERFORMING = {**RIGHT, "total_commercial": 230e6, "he": 104e6, "cards": 3e6, "total_consumer": 107e6}


def rows(quarters):
    return {(key, year, "Q1"): {"group_key": "kpi_loans", "group_kind": "metric", "kpi_key": key, "kpi_label": label,
                                "kpi_order": index, "fiscal_year": year, "fiscal_period": "Q1", "value": figures[key],
                                "unit": "currency", "method": "ai", "validation_status": "verified", "notes": []}
            for year, figures in quarters for index, (key, label) in enumerate(LINES)}


def test_totals_close_the_lines_since_the_last_total_or_the_totals_before():
    assert total_runs(LINES) == [("total_commercial", ("ci", "cre")), ("total_consumer", ("he", "cards")),
                                 ("total", ("total_commercial", "total_consumer"))]
    assert adds_up(RIGHT, ("total", ("total_commercial", "total_consumer")))
    assert adds_up({"a": 1001e6, "b": 2002e6, "t": 4004e6}, ("t", ("a", "b"))) is False
    assert adds_up({"a": 1e9, "b": 2e9, "t": 3e9}, ("t", ("a", "b", "c"))) is None


def test_a_quarter_breaking_a_learned_total_is_held_whole():
    values = rows([*((str(year), RIGHT) for year in range(2016, 2021)), ("2023", NONPERFORMING)])
    held = unbalanced_quarters(values, "unbalanced")
    assert {(v["fiscal_year"], v["validation_status"]) for v in held} == {("2023", "needs_review")}
    assert len(held) == len(LINES)
    # Too few quarters to learn from: nothing held.
    assert unbalanced_quarters(rows([("2016", RIGHT), ("2023", NONPERFORMING)]), "unbalanced") == []


def test_a_replay_breaking_a_learned_total_goes_to_the_ai():
    group = GroupSpec.model_validate({"key": "loans", "label": "Loans by Type", "kind": "metric", "kpis": [
        {"key": key, "label": label, "unit": "currency"} for key, label in LINES]})
    kpis = {kpi.key: kpi for kpi in group.kpis}
    stored = SimpleNamespace(values=rows([(str(year), RIGHT) for year in range(2016, 2021)]))
    read = lambda figures: [SimpleNamespace(kpi=kpis[key], value=value) for key, value in figures.items()]  # noqa: E731
    assert ReplayReading._breaks_learned_totals(stored, group, read(NONPERFORMING))
    assert not ReplayReading._breaks_learned_totals(stored, group, read(RIGHT))
