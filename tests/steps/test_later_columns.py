"""A first reading stands when the next quarter's release prints the same figure for this quarter (p4-finhistory)."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.documents.document import Document, Table
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.readers.extract import ReadValue

SPEC = Spec.model_validate({"groups": [{"key": "deposits", "label": "Average Deposits", "kind": "metric", "kpis": [
    {"key": "nib", "label": "Noninterest-bearing deposits", "unit": "currency"}]}]})


class Control:
    def call(self, operation, **payload):
        return {}


def run(later: Document | None, accepted_row: str = "Noninterest-bearing deposits"):
    pipeline = SymbolPipeline(Control(), "USB", quarters=4, force=False, log=lambda *_: None)
    pipeline.store.load(values=[{"group_key": "kpi_deposits", "kpi_key": "nib", "fiscal_year": "2022", "fiscal_period": "Q4",
                                 "period_end": "2022-12-31", "method": "ai", "validation_status": "verified", "value": 120_827e6,
                                 "locator": {"table": "A_T1", "row": 3, "col": 1, "row_label": accepted_row}}])
    pipeline.profile = SimpleNamespace(name="U.S. Bancorp", fiscal_year_end="1231", foreign=False)
    pipeline.offset = 0
    refs = [SimpleNamespace(accession="Q1", period_end=date(2023, 3, 31)), SimpleNamespace(accession="Q2", period_end=date(2023, 6, 30))]
    pipeline._release_refs = refs
    pipeline._code_document = lambda ref: later if ref.accession == "Q2" else None
    return pipeline


def supplement(q1_figure: str) -> Document:
    document = Document(source_url="https://www.sec.gov/q2.htm")
    document.tables["A_T1"] = Table(id="A_T1", context="", rows=[
        ["AVERAGE DEPOSITS", "", "", "", ""], ["($ in millions)", "", "", "Percent Change", ""],
        ["", "2Q 2023", "1Q 2023", "2Q23 vs1Q23", "YTD 2023"],
        ["Noninterest-bearing deposits", "$113,758", q1_figure, "(12.3)", "$121,705"]])
    document.order.append("A_T1")
    return document


def reading(value: float) -> ReadValue:
    group = SPEC.groups[0]
    return ReadValue(group=group, kpi=group.kpis[0], value=value, currency="USD",
                     locator={"table": "A_T1", "row": 3, "col": 1, "row_label": "Noninterest-bearing deposits"})


def test_the_next_releases_column_for_this_quarter_confirms_a_reading_digit_for_digit():
    q1 = date(2023, 3, 31)
    assert run(supplement("$129,741"))._later_column_matches(reading(129_741e6), q1)
    assert not run(supplement("$129,742"))._later_column_matches(reading(129_741e6), q1)  # one digit off: no proof
    # The same figure in a change or year-to-date column, or no later release, proves nothing.
    assert not run(supplement("$129,741"))._later_column_matches(reading(121_705e6), q1)
    assert not run(None)._later_column_matches(reading(129_741e6), date(2023, 6, 30))


def test_two_filings_printing_a_figure_prove_its_row_only_when_it_is_the_kpis_accepted_row():
    # Truist's summary "Loans and leases" ($314.0 billion) is printed again next quarter as surely as the KPI's own row.
    assert not run(supplement("$129,741"), accepted_row="Total deposits")._later_column_matches(reading(129_741e6), date(2023, 3, 31))


def test_the_next_releases_figure_proves_a_reading_only_in_the_readings_own_measure():
    # The re-check's probe: label, period and digits matched, but in any table. An average proves only an average.
    def own(title: str) -> Document:
        document = Document(source_url="https://www.sec.gov/q1.htm")
        document.tables["A_T1"] = Table(id="A_T1", context="", rows=[
            [title, "", ""], ["($ in millions)", "", ""], ["", "1Q 2023", "4Q 2022"],
            ["Noninterest-bearing deposits", "$129,741", "$120,827"]])
        return document
    q1 = date(2023, 3, 31)
    assert run(supplement("$129,741"))._later_column_matches(reading(129_741e6), q1, own("AVERAGE DEPOSITS"))
    assert not run(supplement("$129,741"))._later_column_matches(reading(129_741e6), q1, own("PERIOD-END DEPOSITS"))
