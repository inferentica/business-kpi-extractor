"""rules.sign_convention."""
from kpi_extractor.rules.sign_convention import opposite_convention


def test_a_row_naming_the_signed_words_the_other_way_round():
    line = "Annualized Net Recoveries (Charge-offs) to Average Loans"
    assert opposite_convention("Net Charge-offs / (Recoveries) (Annualized) to Avg Loans and Leases", line)
    assert not opposite_convention("Annualized Net Recoveries (Charge-offs) to Avg Loans and Leases", line)
    # Northern Trust's earlier wordings name charge-offs as the positive side too.
    assert opposite_convention("Net (Recoveries) / Charge-offs (Annualized) to Avg Loans and Leases", line)
    # A ratio's row naming charge-offs alone states them as the positive side; an amount's row says nothing.
    assert opposite_convention("Net Charge-offs (Annualized) to Average Loans and Leases", line, ratio=True)
    assert not opposite_convention("Net Charge-offs (Annualized) to Average Loans and Leases", line)
    assert not opposite_convention("Investment securities losses", "Investment Securities Gains/(Losses)")
    assert not opposite_convention("Net recoveries to average loans", line, ratio=True)
    # Acronyms and other words in parentheses state no convention.
    assert not opposite_convention("Assets under supervision", "Total Assets Under Supervision (AUS)", ratio=True)
    assert not opposite_convention("Annualized net charge-offs", "Net Charge-offs (Annualized)", ratio=True)
    assert opposite_convention("Net losses (gains) on securities", "Net gains (losses) on securities")
