"""What spares a release its second reading: proofs from the filing itself, settled by code before a reasoning call."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.readers.extract import ReadValue

SPEC = Spec.model_validate({"groups": [
    {"key": "fees", "label": "Fee Revenue", "kind": "revenue_breakdown", "kpis": [
        {"key": "advisory", "label": "Advisory", "unit": "currency"}, {"key": "underwriting", "label": "Underwriting", "unit": "currency"}]},
    {"key": "ops", "label": "Operating", "kind": "metric", "kpis": [{"key": "aum", "label": "Fee-Generating AUM", "unit": "currency"}]}]})


class Control:
    def call(self, operation, **payload):
        return {}


def run(values=()):
    pipeline = SymbolPipeline(Control(), "GS", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Goldman", fiscal_year_end="1231", foreign=False)
    pipeline.store.load(values=list(values))
    return pipeline


def item(group, kpi, value, **locator):
    g = next(g for g in SPEC.groups if g.key == group)
    return ReadValue(group=g, kpi=next(k for k in g.kpis if k.key == kpi), value=value, currency="USD", locator=locator)


def test_a_repeat_whose_source_states_only_its_own_period_needs_no_second_reader():
    # Apollo's fee-generating credit AUM, $135 billion in Q3 2025 and Q3 2026, from a chart that shows only the quarter.
    pipeline = run()
    alone = item("ops", "aum", 135e9, row_numbers=[135.0])
    beside = item("ops", "aum", 135e9, row_numbers=[135.0, 135.0])  # beside the same figure: another column, maybe
    quoted = item("ops", "aum", 135e9, quote="Fee-generating AUM of $135 billion", value_text="$135 billion")
    earlier = item("ops", "aum", 135e9, quote="up from $135 billion a year ago", value_text="$135 billion")
    q3 = date(2026, 9, 30)
    assert not pipeline._repeats(alone, None, [135e9], q3) and not pipeline._repeats(quoted, None, [135e9], q3)
    assert pipeline._repeats(beside, None, [135e9], q3) and pipeline._repeats(earlier, None, [135e9], q3)


def test_a_first_breakdown_adding_up_to_a_figure_reported_in_xbrl_stands_on_one_reading():
    xbrl = {"group_key": "segments", "kpi_key": "ib", "kpi_label": "Investment Banking", "method": "xbrl", "validation_status": "verified",
            "fiscal_year": "2016", "fiscal_period": "Q1", "period_end": "2016-03-31", "value": 1463e6, "locator": {},
            "group_label": "Segments", "group_kind": "revenue_breakdown", "unit": "currency"}
    pipeline = run([xbrl])
    fees = SPEC.groups[0]
    named = [item("fees", "advisory", 771e6, row_label="Advisory"), item("fees", "underwriting", 692e6, row_label="Underwriting")]
    assert pipeline._adds_up_to_xbrl(fees, named, date(2016, 3, 31))
    # A row its label does not name, a sum no XBRL figure reports, or a metric group: a second reader still reads it.
    unnamed = [named[0], item("fees", "underwriting", 692e6, row_label="Total")]
    assert not pipeline._adds_up_to_xbrl(fees, unnamed, date(2016, 3, 31))
    short = [named[0], item("fees", "underwriting", 600e6, row_label="Underwriting")]
    assert not pipeline._adds_up_to_xbrl(fees, short, date(2016, 3, 31))
    assert not pipeline._adds_up_to_xbrl(SPEC.groups[1], named, date(2016, 3, 31))


def test_a_figure_one_ocr_model_alone_read_is_proven_only_digit_for_digit():
    from kpi_extractor.steps.series_checks import ties_out
    from kpi_extractor.steps.shared import equals_named_line, xbrl_figures
    official = xbrl_figures([{"group_key": "segments", "kpi_key": "net", "kpi_label": "Net revenues", "method": "xbrl",
                              "validation_status": "verified", "fiscal_period": "Q3", "period_end": "2026-09-30",
                              "value": 21_932_480e3, "locator": {}}])
    read = {"kpi_label": "Net revenues", "fiscal_period": "Q3", "period_end": "2026-09-30", "value": 21_932_490e3, "notes": []}
    ocr = {**read, "notes": ["read by one OCR model only (DeepSeek)"]}
    assert equals_named_line(official, read) and not equals_named_line(official, ocr)  # ASE's last digit, 0.00005% off
    row = {"value": 21_932_490e3, "notes": [], "locator": {"row_numbers": [21_932_490, 21_932_485]}}
    assert ties_out(row, 21_932_480e3) and not ties_out({**row, "notes": ocr["notes"]}, 21_932_480e3)
