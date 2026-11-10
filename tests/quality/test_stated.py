"""A figure is judged by its filing, never by how far it moved (owner, 2026-11-09)."""
from types import SimpleNamespace

from kpi_extractor.quality.checks import date_order_findings
from kpi_extractor.quality.stated import stated_in_filing
from kpi_extractor.steps.stated_figures import STATED, StatedFigures

PALANTIR = {"quote": "Total contract value (“TCV”) closed of $792 million, including US TCV closed of $588 million",
            "value_text": "$792 million"}
VISA = {"row_label": "Cross-border volume excluding intra-Europe", "row_numbers": [53, -21], "scale": 1}


def test_a_quote_or_a_cell_that_shows_the_figure_states_it():
    assert stated_in_filing("currency", 792e6, PALANTIR)
    assert stated_in_filing("percent", 53, VISA)
    assert stated_in_filing("currency", 1_234e6, {"row_label": "Cloud", "row_numbers": [1234, 980], "scale": 1e6})


def test_a_figure_the_filing_does_not_show_as_it_is_is_not_stated():
    assert not stated_in_filing("currency", 792e9, PALANTIR)  # another scale than the quote's
    assert not stated_in_filing("currency", 792e6, {"quote": "TCV of 792", "value_text": "792"})  # scale from elsewhere
    assert not stated_in_filing("percent", 35, VISA)
    assert not stated_in_filing("currency", 1_234e9, {"row_label": "Cloud", "row_numbers": [1234], "scale": 1e6})
    assert not stated_in_filing("currency", 1_234e6, {"row_label": "Cloud", "row_numbers": [1234], "scale": 1e6},
                                ["read without its table's scale: 1000× to match the series"])
    assert not stated_in_filing("currency", 5e6, {})
    # A cell in a row labelled with a date, not the KPI (Visa's Q4 2017 cross-border growth).
    assert not stated_in_filing("percent", 10, {"row_label": "September 30, 2017", "row_numbers": [10], "scale": 1},
                                label="Cross-Border Volume Growth Total")
    assert stated_in_filing("percent", 53, VISA, label="Cross-Border Volume Growth Excluding Intra-Europe")


def _row(period_end, value, locator=None, **fields):
    return {"group_key": "kpi_operating", "kpi_key": "tcv", "fiscal_year": period_end[:4], "fiscal_period": "Q2",
            "period_end": period_end, "value": value, "unit": "currency", "group_kind": "metric", "method": "ai", "validation_status": "verified",
            "notes": [], "locator": locator, **fields}


def test_a_sharp_change_the_filing_states_is_no_finding():
    key = lambda row: row["period_end"]  # noqa: E731
    stated = [_row("2022-03-31", 248e6, {"quote": "TCV closed of $248 million", "value_text": "$248 million"}),
              _row("2022-06-30", 792e6, PALANTIR)]
    assert date_order_findings(stated, None, key) == {}
    unconfirmed = [_row("2022-03-31", 248e6), _row("2022-06-30", 792e6)]
    assert date_order_findings(unconfirmed, None, key) == {"2022-06-30": "changed 3.2x in a quarter"}


class _Company(StatedFigures):
    def __init__(self, values):
        self.values, self.symbol, self.logged = values, "PLTR", []

    def log(self, line):
        self.logged.append(line)


def test_figures_held_only_for_a_jump_that_their_filing_states_stand():
    held = lambda value, locator, notes, **fields: _row("2022-06-30", value, locator, validation_status="needs_review",  # noqa: E731
                                                         notes=notes, **fields)
    company = _Company({
        "pltr": held(792e6, PALANTIR, ["changed 3.2x in a quarter"]),
        "visa": {**held(53, VISA, ["moved +74.0 points in a quarter", "checked against 0001403161-22-000055"]), "unit": "percent"},
        "unstated": held(792e6, {}, ["changed 3.2x in a quarter"]),
        "other_reason": held(792e6, PALANTIR, ["changed 3.2x in a quarter", "parts differ from the total by 9.0%"]),
        "owner": held(792e6, PALANTIR, ["changed 3.2x in a quarter"], owner_decision="rejected"),
    })
    stood = {row["value"]: row for row in company._stand_stated_figures()}
    assert sorted(stood) == [53, 792e6] and len(company._stand_stated_figures()) == 2
    assert all(row["validation_status"] == "verified" and row["notes"] == [STATED] for row in stood.values())


def test_a_fall_the_quote_states_in_words_is_stated():
    # Mastercard Q2 2020: "A decrease in cross-border volume of 45%" is the -45% stored; held as a 44-point jump until now.
    quote = {"quote": "A decrease in cross-border volume of 45% on a local currency basis.", "value_text": "45%"}
    assert stated_in_filing("percent", -45, quote)
    # The same number with no fall in the words is not this figure.
    assert not stated_in_filing("percent", -45, {"quote": "Cross-border volume grew 45% on a local currency basis.", "value_text": "45%"})


def test_a_bare_quoted_number_is_stated_when_the_filing_states_its_scale_nearby():
    # Apollo's "($ in billions)" heading over "AUM of $908": the reading recorded where the scale came from.
    locator = {"quote": "Total AUM of $908", "value_text": "$908",
               "scale_from": {"factor": 1e9, "source": "passage", "words": "($ in billions)"}}
    assert stated_in_filing("currency", 908e9, locator)
    # The reader's own scale is no statement of the filing's.
    assert not stated_in_filing("currency", 908e9, {**locator, "scale_from": {"factor": 1e9, "source": "reader"}})
