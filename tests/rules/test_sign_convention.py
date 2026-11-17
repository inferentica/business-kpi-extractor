"""rules.sign_convention."""
from kpi_extractor.rules.sign_convention import opposite_convention


def test_a_row_naming_the_signed_words_the_other_way_round():
    line = "Annualized Net Recoveries (Charge-offs) to Average Loans"
    assert opposite_convention("Net Charge-offs / (Recoveries) (Annualized) to Avg Loans and Leases", line)
    assert not opposite_convention("Annualized Net Recoveries (Charge-offs) to Avg Loans and Leases", line)
    assert not opposite_convention("Net charge-offs to average loans", line)  # no convention stated: nothing to turn
    assert opposite_convention("Net losses (gains) on securities", "Net gains (losses) on securities")
