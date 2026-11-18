"""rules.period: bounds and trailing twelve months in a quoted figure's words."""
from kpi_extractor.rules.period import bound_problem, quote_problem, trailing_problem


def test_a_bound_is_not_the_figure():
    assert bound_problem("Global Systemwide sales increased 4% to over ", "$38 billion")
    assert bound_problem("Oracle also delivered ", "more than 300,000 GPUs")
    assert bound_problem("we announced deployments of ", "nearly $10 billion")
    assert bound_problem("Sales were approximately ", "$9 billion") is None
    assert bound_problem("increased 5% to ", "$37 billion") is None
    assert bound_problem("over 30 contracts totaling ", "$12.5 billion") is None  # the bound is another figure's


def test_trailing_twelve_months_before_every_figure_is_not_the_quarter():
    alone = "Systemwide sales to loyalty members for the trailing twelve months increased over 20% to $40 billion"
    assert trailing_problem(alone, alone, alone[:alone.find("$40")], "Systemwide Sales to Loyalty Members")
    both = ("Systemwide sales to loyalty members were approximately $33 billion for the trailing twelve-month period "
            "and approximately $9 billion for the quarter")
    assert trailing_problem(both, both, both[:both.find("$9")], "Systemwide Sales to Loyalty Members") is None
    assert trailing_problem(alone, alone, alone[:alone.find("$40")], "Loyalty Sales (TTM)") is None  # the line is the year's
    assert quote_problem(alone, alone, alone[:alone.find("$40")], "$40 billion", "Systemwide Sales to Loyalty Members")
