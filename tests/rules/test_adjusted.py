"""rules.period.adjusted_problem."""
from kpi_extractor.rules.period import adjusted_problem

SLIDE = ("Net Interest Income and Margin Highlights. Excluding PAA, 4Q19 net interest income was $972 MM and net interest "
         "margin was 2.93%. Net interest income down $13 MM, or 1%, from 4Q18.")


def test_a_sentence_opening_with_an_adjustment_gives_the_adjusted_figure():
    assert adjusted_problem(SLIDE, "interest margin was 2.93%", "Net Interest Margin") == \
        "the sentence gives the figure 'excluding paa', not the reported one"
    assert adjusted_problem("On an adjusted basis, revenue rose 4%.", "revenue rose 4%", "Revenue Growth")
    # A line named for the adjusted figure is that figure.
    assert adjusted_problem(SLIDE, "interest margin was 2.93%", "Net Interest Margin excl. PAA") is None
    # A scope inside the measure's own phrase, or a sentence that opens otherwise, says nothing.
    assert adjusted_problem("CRD new bookings, excluding affiliates, of $5 million.", "new bookings, excluding affiliates, of $5 million",
                            "Software New Bookings") is None
    assert adjusted_problem("The net interest margin was 3.18%, excluding PAA 3.09%.", "the net interest margin was 3.18%",
                            "Net Interest Margin") is None
