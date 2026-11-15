from datetime import date

from statement_extractor.domain.model import Reading
from statement_extractor.selection.signs import settle_signs


def _reading(accession: str, value: float, *, source: str = "html", template: bool = False) -> Reading:
    return Reading(statement="income", duration="year", fiscal_year="2024", fiscal_period="FY", period_end=date(2024, 12, 31),
                   period_start=date(2024, 1, 1), currency="MXN", accession=accession, form="6-K", filed=date(2025, 4, 1),
                   own=True, source=source, template=template,
                   lines=[{"line": 0, "concept": "ifrs-full_CostOfSales", "label": "Cost of sales", "level": 0,
                           "is_heading": False, "value": value}])


def test_a_printed_templates_positive_costs_never_flip_a_filings_sign():
    # Round 67: Televisa's quarterly XBRL reports, printed in its 6-Ks with costs as positive amounts, outvoted its
    # 2024 20-F's own sign for its cost of sales.
    winner = _reading("0001104659-26-052976", -41_117_033_000, source="xbrl")
    reports = [_reading(f"0000895345-25-00007{n}", 41_117_033_000, template=True) for n in range(3)]
    settled = settle_signs(winner, [winner, *reports, _reading("0000895345-25-000071", -41_117_033_000)])
    assert settled.lines[0]["value"] == -41_117_033_000
    flipped = settle_signs(winner, [winner, *[_reading(r.accession, r.lines[0]["value"]) for r in reports]])
    assert flipped.lines[0]["value"] == 41_117_033_000  # the same votes from presentations of the company's own


def test_the_template_mark_stays_out_of_the_served_reading():
    assert "template" not in _reading("x", 1.0, template=True).to_json()
