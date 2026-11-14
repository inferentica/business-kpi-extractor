"""Arelle's verdicts on columns read from XBRL (checks/arelle.py), from results shaped as checks/arelle_run.py prints
them: no Arelle or network needed."""
from datetime import date
from types import SimpleNamespace

from statement_extractor.checks.arelle import Checks, Facts, check_filing, compare, instance_url
from statement_extractor.domain.model import Reading


def _reading(statement, start, end, lines):
    return Reading(statement=statement, duration="year", fiscal_year="2025", fiscal_period="FY", period_end=end,
                   period_start=start, currency="USD", accession="a", form="10-K", filed=date(2026, 2, 12),
                   own=True, source="xbrl", lines=lines)


def _line(concept, value, **extra):
    return {"concept": concept, "label": concept, "level": 0, "is_heading": False, "value": value, **extra}


PRU_LIKE = {
    "facts": [
        ["us-gaap_Assets", None, "2025-12-31", False, 773740000000.0, "-6"],
        ["us-gaap_Liabilities", None, "2025-12-31", False, 738159000000.0, "-6"],
        ["us-gaap_Assets", None, "2025-12-31", True, 506270000000.0, "-6"],  # the parent alone (Schedule II)
        ["us-gaap_NetCashProvidedByUsedInFinancingActivities", "2025-01-01", "2025-12-31", False, -2827000000.0, "-6"],
        ["us-gaap_CashAndCashEquivalentsAtCarryingValue", None, "2024-12-31", False, 18000000000.0, "-6"],
        ["us-gaap_CashAndCashEquivalentsAtCarryingValue", None, "2025-12-31", False, 19000000000.0, "-6"],
    ],
    "calc": [
        ["us-gaap_NetCashProvidedByUsedInFinancingActivities", "2025-01-01", "2025-12-31", False,
         "[-2827500000, -2826500000]", "[-2824500000.0, -2817500000.0]", "role/CashFlows"],
        ["us-gaap_Assets", None, "2025-12-31", True, "[1, 2]", "[3, 4]", "role/Position"],  # a dimension: not ours
    ],
    "sums": ["us-gaap_Assets", "us-gaap_NetCashProvidedByUsedInFinancingActivities"],
}


def test_served_figures_found_among_the_filings_facts():
    balance = _reading("balance", None, date(2025, 12, 31), [
        _line("us-gaap_Assets", 773740000000.0), _line("us-gaap_Liabilities", 738159000000.0)])
    verdict = compare(balance, Facts(PRU_LIKE))
    assert verdict["facts"] == "pass" and verdict["matched"] == 2
    assert verdict["calc"] == "pass"  # total assets is a declared sum, and Arelle found it holds company-wide


def test_a_figure_the_filing_tags_otherwise_is_a_misread():
    balance = _reading("balance", None, date(2025, 12, 31), [_line("us-gaap_Assets", 77374000000.0)])
    verdict = compare(balance, Facts(PRU_LIKE))
    assert verdict["facts"] == "fail" and "Assets" in verdict["detail"]


def test_an_inconsistent_sum_in_the_columns_own_period_fails_calc():
    flows = _reading("cash_flow", date(2025, 1, 1), date(2025, 12, 31), [
        _line("us-gaap_NetCashProvidedByUsedInFinancingActivities", -2827000000.0),
        _line("us-gaap_CashAndCashEquivalentsAtCarryingValue", 18000000000.0),  # opening cash: the day before
        _line("us-gaap_CashAndCashEquivalentsAtCarryingValue", 19000000000.0)])
    verdict = compare(flows, Facts(PRU_LIKE))
    assert verdict["facts"] == "pass" and verdict["matched"] == 3
    assert verdict["calc"] == "fail" and "NetCashProvidedByUsedInFinancingActivities" in verdict["detail"]


def test_lines_arelle_has_no_fact_for_compare_nothing():
    income = _reading("income", date(2025, 1, 1), date(2025, 12, 31), [_line("pru_OwnLine", 5.0)])
    verdict = compare(income, Facts(PRU_LIKE))
    assert (verdict["facts"], verdict["calc"], verdict["missing"]) == ("none", "none", 1)


def test_a_breakdown_line_matches_the_filings_dimensional_facts():
    balance = _reading("balance", None, date(2025, 12, 31), [_line("us-gaap_Assets", 506270000000.0, member="Parent")])
    assert compare(balance, Facts(PRU_LIKE))["facts"] == "pass"


def _attachment(name, kind):
    return SimpleNamespace(document=name, document_type=kind, url=f"https://www.sec.gov/x/{name}")


def test_the_instance_filed_else_edgars_extract_else_the_inline_document():
    filed = SimpleNamespace(attachments=SimpleNamespace(data_files=[
        _attachment("aapl-20091226.xsd", "EX-101.SCH"), _attachment("aapl-20091226.xml", "EX-101.INS")]))
    assert instance_url(filed).endswith("aapl-20091226.xml")
    inline = SimpleNamespace(attachments=SimpleNamespace(data_files=[
        _attachment("R1.htm", "HTML"), _attachment("tsm-20251231_htm.xml", "XML")]))
    assert instance_url(inline).endswith("tsm-20251231_htm.xml")
    bare = SimpleNamespace(attachments=SimpleNamespace(data_files=[]),
                           document=SimpleNamespace(url="https://www.sec.gov/x/doc.htm"))
    assert instance_url(bare).endswith("doc.htm")


def test_a_filing_arelle_cannot_read_leaves_its_readings_unchecked(monkeypatch):
    import statement_extractor.checks.arelle as arelle
    monkeypatch.setattr(arelle, "run_arelle", lambda url, cache: {"error": "timed out after 300 s"})
    reading = _reading("balance", None, date(2025, 12, 31), [_line("us-gaap_Assets", 1.0)])
    filing = SimpleNamespace(attachments=SimpleNamespace(data_files=[_attachment("x.xml", "EX-101.INS")]))
    record = check_filing(filing, [reading], "cache")
    assert record["error"].startswith("timed out") and reading.arelle is None
    assert reading.status == "unproven"  # Arelle never decides a status


def test_an_opening_balance_tagged_on_the_periods_first_day_matches():
    # Visteon's fresh start: the successor's opening cash tagged at 2010-10-01, the day its period starts.
    flows = _reading("cash_flow", date(2010, 10, 1), date(2010, 12, 31), [
        _line("us-gaap_CashAndCashEquivalentsAtCarryingValue", 866e6), _line("us-gaap_CashAndCashEquivalentsAtCarryingValue", 905e6)])
    facts = Facts({"facts": [["us-gaap_CashAndCashEquivalentsAtCarryingValue", None, "2010-10-01", False, 866e6, "-6"],
                             ["us-gaap_CashAndCashEquivalentsAtCarryingValue", None, "2010-12-31", False, 905e6, "-6"]]})
    assert compare(flows, facts)["facts"] == "pass"


def test_filings_past_the_companys_budget_are_left_unchecked(monkeypatch):
    import statement_extractor.checks.arelle as arelle
    monkeypatch.setattr(arelle, "run_arelle", lambda url, cache, timeout=300: {"facts": [], "seconds": 1.0})
    checks = Checks("cache")
    filing = SimpleNamespace(attachments=SimpleNamespace(data_files=[_attachment("x.xml", "EX-101.INS")]))
    first, late = {}, {}
    checks.submit(filing, [_reading("balance", None, date(2025, 12, 31), [_line("us-gaap_Assets", 1.0)])], first)
    checks.finish()
    checks = Checks("cache")
    checks.deadline = 1.0  # long past
    checks.submit(filing, [_reading("balance", None, date(2025, 12, 31), [_line("us-gaap_Assets", 1.0)])], late)
    checks.finish()
    assert "error" not in first["arelle"] and first["arelle"]["url"].endswith("x.xml")
    assert late["arelle"]["error"].startswith("over")
