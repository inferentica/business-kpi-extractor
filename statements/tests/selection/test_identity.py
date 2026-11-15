"""One key per line across a company's eras (selection/identity.py)."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.selection.identity import apply_identity, line_identity


def reading(accession, filed, year, lines, basis="US GAAP"):
    return Reading(statement="income", duration="year", fiscal_year=year, fiscal_period="FY",
                   period_end=date(int(year), 12, 31), period_start=None, currency="USD", accession=accession,
                   form="10-K", filed=filed, own=False, source="xbrl" if accession.startswith("x") else "html",
                   basis=basis, checks=[Check("calc", "pass")],
                   lines=[{"line": i, "concept": c, "label": label, "level": 0, "is_heading": False, "value": v}
                          for i, (c, label, v) in enumerate(lines)])


def test_a_line_followed_from_filing_to_filing_takes_the_xbrl_concept():
    # Apple: "Net sales" printed in 2006-07, "Total net sales" in 2007-08, tagged in the FY2009 10-K's 2008 column.
    readings = [
        reading("h2007", date(2007, 11, 15), "2006", [("printed_net_sales", "Net sales", 19_315e6), ("printed_cost_of_sales", "Cost of sales", 13_717e6)]),
        reading("h2008", date(2008, 11, 5), "2006", [("printed_total_net_sales", "Total net sales", 19_315e6), ("printed_cost_of_sales", "Cost of sales", 13_717e6)]),
        reading("h2008", date(2008, 11, 5), "2008", [("printed_total_net_sales", "Total net sales", 32_479e6), ("printed_cost_of_sales", "Cost of sales", 21_334e6)]),
        reading("x2009", date(2009, 10, 27), "2008", [("us-gaap_SalesRevenueNet", "Net sales", 32_479e6), ("us-gaap_CostOfGoodsSold", "Cost of sales", 21_334e6)]),
    ]
    keys = line_identity(readings)
    assert keys[("income", "printed_net_sales", "")] == "us-gaap_SalesRevenueNet"
    assert keys[("income", "printed_total_net_sales", "")] == "us-gaap_SalesRevenueNet"
    assert keys[("income", "printed_cost_of_sales", "")] == "us-gaap_CostOfGoodsSold"


def test_lines_printed_side_by_side_or_alike_by_chance_stay_apart():
    readings = [
        # one period alone, labels sharing no word: a coincidence (TSMC's "Loss on lease modification" and "Others")
        reading("h1", date(2010, 3, 1), "2009", [("printed_loss_on_lease_modification", "Loss on lease modification", 5_000e3)]),
        reading("x1", date(2011, 3, 1), "2009", [("ifrs-full_OtherAdjustments", "Decrease in others", 5_000e3)], basis="US GAAP"),
        # printed side by side in one statement: two lines, though another filing shows one's figure on the other
        reading("h2", date(2012, 3, 1), "2011", [("printed_a", "Product revenue", 7e6), ("printed_b", "Product revenue other", 9e6)]),
        reading("h3", date(2013, 3, 1), "2011", [("printed_b", "Product revenue other", 7e6)]),
        # per-share figures never vote
        reading("h4", date(2014, 3, 1), "2013", [("printed_eps_before_tax", "Before income tax", 1.15), ("printed_x", "Sales", 1e9)]),
        reading("h5", date(2015, 3, 1), "2013", [("printed_eps_diluted", "Earnings per share diluted", 1.15), ("printed_x", "Sales", 1e9)]),
    ]
    keys = line_identity(readings)
    assert keys.get(("income", "printed_loss_on_lease_modification", ""), "printed_loss_on_lease_modification") == "printed_loss_on_lease_modification"
    assert keys.get(("income", "printed_a", ""), "printed_a") != keys.get(("income", "printed_b", ""), "printed_b")
    assert keys.get(("income", "printed_eps_before_tax", ""), "printed_eps_before_tax") == "printed_eps_before_tax"


def test_served_lines_get_their_key_and_keep_their_concept():
    readings = [reading("h", date(2008, 11, 5), "2008", [("printed_total_net_sales", "Total net sales", 32_479e6)]),
                reading("x", date(2009, 10, 27), "2008", [("us-gaap_SalesRevenueNet", "Net sales", 32_479e6)])]
    chosen = [{"statement": "income", "lines": [dict(readings[0].lines[0])]}]
    apply_identity(chosen, readings)
    line = chosen[0]["lines"][0]
    assert (line["concept"], line["key"], line["value"]) == ("printed_total_net_sales", "us-gaap_SalesRevenueNet", 32_479e6)
