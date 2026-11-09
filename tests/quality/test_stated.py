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
