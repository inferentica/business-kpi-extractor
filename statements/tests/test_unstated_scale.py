"""Statements that print no scale are read only at a scale the filing's own figures prove."""
from datetime import date

from statement_extractor.checks.schedules import Schedule
from statement_extractor.domain.model import Reading
from statement_extractor.pipeline.unstated_scale import proven_scales


def reading(statement, *pairs, duration="instant"):
    return Reading(statement=statement, duration=duration, fiscal_year="1997", fiscal_period="Q3",
                   period_end=date(1997, 9, 30), period_start=None, currency="USD", accession="a", form="10-Q",
                   filed=date(1997, 11, 14), own=True, source="text",
                   lines=[{"line": n, "concept": "printed", "label": label, "level": 0, "is_heading": False, "value": value}
                          for n, (label, value) in enumerate(pairs)], checks=[])


def test_whole_dollars_proven_by_the_schedule_and_cash_by_the_balance_sheet():
    # Icahn's 1997 10-Q prints whole dollars, no caption; its EX-27 states thousands.
    schedule = Schedule(date(1997, 9, 30), "ytd", 1000.0, 966_144_000.0, 67_639_000.0)
    balance = reading("balance", ("Cash and cash equivalents", 120_412_345.0), ("Total", 966_144_480.0))
    income = reading("income", ("Revenues", 100_000_000.0), ("Net earnings", 67_639_210.0), duration="ytd")
    flows = reading("cash_flow", ("Net earnings", 67_639_210.0),
                    ("Cash and cash equivalents, end of period", 120_412_345.0), duration="ytd")
    assert proven_scales([balance, income, flows], [schedule], []) == {"balance": 1.0, "income": 1.0, "cash_flow": 1.0}


def test_no_proof_no_scale():
    unproven = Schedule(date(1997, 9, 30), "ytd", 1000.0, 5_000_000.0, 1_000.0)
    balance = reading("balance", ("Total", 966_144_480.0))
    assert proven_scales([balance], [unproven], []) == {}
    assert proven_scales([balance], [], []) == {}


def test_a_balance_sheet_proven_by_its_filings_cash_flow():
    # Copel's Q1 2004 report: the CVM form's balance sheet states no scale; its cash flow, "(In thousands of reais)",
    # closes at 345,337 on the same day.
    balance = reading("balance", ("Cash", 345_337.0), ("Total assets", 9_221_326.0))
    flows = reading("cash_flow", ("Net income (loss) for the quarter", 89_711_000.0), ("Closing balance", 345_337_000.0),
                    duration="ytd")
    assert proven_scales([balance], [], [flows]) == {"balance": 1000.0}
