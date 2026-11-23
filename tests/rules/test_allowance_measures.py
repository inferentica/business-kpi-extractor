"""The allowance for credit losses is never read from the allowance for loan and lease losses (PNC, 2026-11-23)."""
from kpi_extractor.ai import Located, Locator, Spec
from kpi_extractor.documents.document import Document, Table
from kpi_extractor.readers.extract import read_values
from kpi_extractor.rules.identity import MEASURE_ROW, measure_row

SPEC = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric", "kpis": [
    {"key": "acl_to_loans", "label": "Allowance for Credit Losses to Total Loans", "unit": "percent"}]}]})
Q2_2025 = Table("A_T18", "The PNC Financial Services Group, Inc. Allowance for Credit Losses", [
    ["", "June 30 2025", "March 31 2025", "June 30 2024"],
    ["Nonperforming loans to total loans", "0.68%", "0.66%", "0.71%"],
    ["Allowance for loan and lease losses to total loans", "1.39%", "1.43%", "1.44%"],
    ["Allowance for credit losses to total loans", "1.62%", "1.64%", "1.67%"]])
Q4_2019 = Table("A_T60", "The PNC Financial Services Group, Inc. Asset Quality", [
    ["", "December 31 2019", "September 30 2019"], ["Allowance for loan and lease losses to total loans", "1.14%", "1.15%"]])
Q1_2020 = Table("A_T40", "The PNC Financial Services Group, Inc. Asset Quality", [
    ["", "March 31 2020", "December 31 2019"], ["Allowance for loans and leases to total loans (k)", "1.49%", "1.14%"]])


def document(table: Table) -> Document:
    doc = Document(source_url="https://www.sec.gov/x.htm")
    doc.tables[table.id] = table
    doc.order.append(table.id)
    return doc


def reading(table: Table, row: int, label: str) -> Located:
    return Located(period_end="2025-06-30", tables=[], values=[
        Locator(kpi="operating.acl_to_loans", table=table.id, row=row, col=1, row_label=label)])


def test_the_credit_loss_ratio_is_read_from_its_own_row_beside_the_loan_and_lease_one():
    read, problems = read_values(document(Q2_2025), SPEC, reading(Q2_2025, 2, Q2_2025.rows[2][0]), "USD")
    assert [(item.value, item.locator["row"]) for item in read] == [(1.62, 3)] and problems == []
    assert any(note.startswith(MEASURE_ROW) for note in read[0].notes)
    # Read from its own row it stays as it is.
    read, _ = read_values(document(Q2_2025), SPEC, reading(Q2_2025, 3, Q2_2025.rows[3][0]), "USD")
    assert [(item.value, item.notes) for item in read] == [(1.62, [])]


def test_before_cecl_the_loan_and_lease_ratio_is_no_credit_loss_ratio():
    for table in (Q4_2019, Q1_2020):
        read, problems = read_values(document(table), SPEC, reading(table, 1, table.rows[1][0]), "USD")
        assert read == [] and "allowance for loan and lease losses" in problems[0]


def test_labels_naming_neither_measure_or_the_same_one_are_left_alone():
    locator = Locator(kpi="operating.acl_to_loans", table="A_T18", row=1, col=1, row_label="Nonperforming loans to total loans")
    assert measure_row(Q2_2025, locator, "Nonperforming loans to total loans", "Nonperforming Loans to Total Loans") == (locator, [], None)
    assert measure_row(Q2_2025, locator, "Allowance for credit losses", "Allowance for Credit Losses") == (locator, [], None)
