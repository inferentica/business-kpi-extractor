"""A statement naming no currency takes the one its document's other statements all name."""
from datetime import date

from statement_extractor.domain.model import Reading
from statement_extractor.pipeline.printed import share_currency


def reading(statement, currency):
    return Reading(statement=statement, duration="quarter", fiscal_year="2022", fiscal_period="Q1",
                   period_end=date(2022, 3, 31), period_start=None, currency=currency, accession="a", form="6-K",
                   filed=date(2022, 5, 6), own=True, source="html", lines=[], checks=[])


def test_petrobras_real_release_cash_flow_takes_the_real():
    found = [reading("balance", "BRL"), reading("income", "BRL"), reading("cash_flow", None)]
    share_currency(found)
    assert [item.currency for item in found] == ["BRL", "BRL", "BRL"]


def test_two_currencies_named_leave_it_unnamed():
    found = [reading("balance", "BRL"), reading("income", "USD"), reading("cash_flow", None)]
    share_currency(found)
    assert found[2].currency is None
