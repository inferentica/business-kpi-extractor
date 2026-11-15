"""A statement naming no currency takes the one its document's other statements all name."""
from datetime import date

from statement_extractor.domain.model import Reading
from statement_extractor.pipeline.printed import share_currency


def reading(statement, currency, scope="consolidated"):
    return Reading(scope=scope, statement=statement, duration="quarter", fiscal_year="2022", fiscal_period="Q1",
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


def test_one_parent_table_naming_a_currency_names_nothing_for_the_rest():
    # Scotiabank's 2015 report: its parent-only income names US dollars; its group statements are in Canadian dollars.
    found = [reading("income", "USD", scope="parent"), reading("balance", None), reading("income", None)]
    share_currency(found)
    assert [item.currency for item in found] == ["USD", None, None]
