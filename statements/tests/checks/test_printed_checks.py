from statement_extractor.checks.printed import total_check
from statement_extractor.checks.subtotals import subtotals


def lines(*pairs):
    return [(label, 0, value) for label, value in pairs]


def test_a_total_proven_as_a_difference_counts():
    # JPM 2003: total net revenue is revenue before the provision less the provision.
    printed = lines(("Total noninterest revenue", 4700), ("Interest income", 5696), ("Interest expense", 2648),
                    ("Net interest income", 3048), ("Revenue before provision for credit losses", 7748),
                    ("Provision for credit losses", 223), ("Total net revenue", 7525))
    assert total_check(printed).result == "pass"


def test_a_total_first_under_its_heading_is_not_tested():
    # TSMC 2005: a supplemental block's "Total acquisitions" after the statement's closing balance.
    printed = lines(("Cash and cash equivalents, end of year", 96483.7)) + [("Cash paid for acquisition", 0, None)] + \
        lines(("Total acquisitions", 56166.2), ("Increase in payables", -1000.0))
    assert total_check(printed).result == "none"


def test_figures_in_printed_tenths_need_an_exact_sum():
    # TSMC 2015, in tenths of a million: 325 - 313 is not 0.
    printed = lines(("Long-term bank loans", 325), ("Deferred income tax liabilities", 313), ("Obligations under finance leases", 0))
    assert subtotals(printed) == {}
