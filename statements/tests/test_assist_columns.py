import json
from datetime import date

import pytest

from statement_extractor.assist.client import Client, is_peak
from statement_extractor.assist.columns import AnswerError, column_prompt, cost_cny, parse_columns
from statement_extractor.assist.pilot import agrees, rule_map
from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.find import find_statements

def _row(label, *cells):
    return "<tr><td>" + label + "</td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


# FEMSA's FY2011 20-F, cut down (as tests/readers/test_sign_columns.py).
_TABLE = ("<p>FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. AND SUBSIDIARIES Consolidated Income Statements For the years "
          "ended December 31, 2011 and 2010. Amounts expressed in millions of U.S. dollars ($) and in millions of Mexican "
          "pesos (Ps.).</p><table><tr><td></td><td colspan='4'>2011</td><td colspan='2'>2010</td></tr>"
          + _row("Net sales", "$", "14,470", "Ps.", "201,867", "Ps.", "168,376")
          + _row("Cost of sales", "", "8,459", "", "118,009", "", "98,732")
          + _row("Gross profit", "", "6,011", "", "83,858", "", "69,644")
          + _row("Income before income taxes", "", "2,033", "", "28,371", "", "23,632")
          + _row("Income taxes", "", "507", "", "7,077", "", "5,671")
          + _row("Net income", "$", "1,526", "Ps.", "21,294", "Ps.", "17,961") + "</table>")


def _group():
    return next(group for kind, group in find_statements(parse_html(_TABLE)) if kind == "income")


def test_the_prompt_shows_each_cell_at_its_position_and_no_more():
    prompt = column_prompt("income", _group())
    assert "Figure positions:" in prompt and "Ps." in prompt and "201,867" in prompt


def test_an_answer_names_only_the_tables_positions():
    group = _group()
    positions = sorted(rule_map("income", group, date(2011, 12, 31)))
    good = {"columns": [{"position": positions[0], "period_end": "2011-12-31", "months": 12, "currency": "USD"}]}
    assert parse_columns(json.dumps(good), group[0])[positions[0]]["currency"] == "USD"
    with pytest.raises(AnswerError):
        parse_columns(json.dumps({"columns": [{"position": 999, "period_end": "2011-12-31", "months": 12, "currency": "MXN"}]}), group[0])
    with pytest.raises(AnswerError):
        parse_columns('{"columns": [{"position": 1, "period_end": "2011-02-31", "months": 12, "currency": "MXN"}]}', group[0])


def test_the_rules_map_is_the_answer_key():
    rules = rule_map("income", _group(), date(2011, 12, 31))
    translated = [position for position, rule in rules.items() if rule["translation"]]
    assert len(translated) == 1 and len(rules) == 3
    answer = {position: {"end": rule["end"], "months": rule["months"],
                         "currency": "USD" if rule["translation"] else "MXN"} for position, rule in rules.items()}
    assert agrees(rules, answer, "MXN") == (True, [])
    answer[translated[0]]["currency"] = "MXN"  # the translation read as pesos: a second 2011
    assert agrees(rules, answer, "MXN")[0] is False


def test_a_cached_answer_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    client = Client(tmp_path)
    with pytest.raises(RuntimeError, match="OIDC is unavailable"):
        client.ask("system", "user")  # outside GitHub Actions nothing is asked
    import hashlib
    from statement_extractor.assist.client import MODEL
    from statement_extractor.assist.columns import PROMPT_VERSION
    digest = hashlib.sha256(f"{PROMPT_VERSION}\n{MODEL}\nsystem\nuser".encode()).hexdigest()
    (tmp_path / f"{digest}.json").write_text(json.dumps({"content": '{"columns": []}'}))
    assert client.ask("system", "user") == '{"columns": []}' and client.cached == 1 and client.calls == 0


def test_cost_in_yuan_by_deepseeks_list():
    usage = {"prompt_cache_hit_tokens": 2_000_000, "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert cost_cny(usage, peak=True) == pytest.approx(0.08 + 2 + 8)
    assert cost_cny(usage, peak=False) == pytest.approx((0.08 + 2 + 8) / 2)
    from datetime import datetime
    assert is_peak(datetime(2026, 11, 12, 7)) and not is_peak(datetime(2026, 11, 14, 7))
