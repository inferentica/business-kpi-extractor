"""The column-map pilot: for each statement table of the pilot 20-Fs, DeepSeek's column map beside the rules' (proven
by the statement's arithmetic), with calls, ¥ and seconds. python -m statement_extractor.assist.pilot <out dir>"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from ..readers.html.parse import parse_html
from ..readers.printed.currency import column_currencies, reporting_currency, split_by_sign, translations
from ..readers.printed.dates import column_periods
from ..readers.printed.find import find_statements, two_columns
from ..readers.printed.scales import statement_scale
from ..sources.listing import filing_for
from .client import Client
from .columns import SYSTEM, AnswerError, column_prompt, parse_columns

# FEMSA's and Coca-Cola FEMSA's 20-Fs with a dollar translation under one heading beside the pesos; then ordinary layouts.
PILOT = [("FMX", "0001193125-12-191729", date(2011, 12, 31)), ("KOF", "0001292814-12-001068", date(2011, 12, 31)),
         ("KOF", "0001292814-11-001761", date(2010, 12, 31)), ("KOF", "0001193125-14-140770", date(2013, 12, 31))]
CONTROLS = [("TV", "0000950123-11-062480", date(2010, 12, 31)), ("AMX", "0001193125-10-128293", date(2009, 12, 31)),
            ("KEP", "0001193125-10-149420", date(2009, 12, 31)), ("SID", "0001292814-17-003424", date(2016, 12, 31))]


def _months(period) -> int:
    if period.start is None:
        return 0
    return (period.end.year - period.start.year) * 12 + period.end.month - period.start.month + (period.end.day >= 28)


def rule_map(kind: str, group, document_end: date) -> dict[int, dict]:
    """Each figure position's period and currency as the rules read them, and whether it is a translation."""
    periods = split_by_sign(group[0], column_periods(kind, group, "1231", document_end))
    dated = sorted(set(periods) - two_columns(group[0], periods))
    by_column = column_currencies(group[0], dated)
    currency = reporting_currency(group, by_column, "20-F")
    dropped = translations(dated, periods, by_column, currency, group)
    return {position: {"end": periods[column].end, "months": _months(periods[column]),
                       "currency": by_column.get(column) or currency, "translation": column in dropped}
            for column in dated for position in column}


def agrees(rules: dict, answer: dict, reporting: str | None) -> tuple[bool, list[str]]:
    """The answer agrees where every column the rules read has its period, the translations another currency, and no
    position the rules leave out is given a period of the reporting currency."""
    faults = []
    for position, rule in rules.items():
        got = answer.get(position)
        if got is None:
            faults.append(f"{position}: missing")
        elif (got["end"], got["months"]) != (rule["end"], rule["months"]):
            faults.append(f"{position}: {got['end']} {got['months']}m, rules {rule['end']} {rule['months']}m")
        elif rule["translation"] == (got["currency"] == reporting):
            faults.append(f"{position}: currency {got['currency']}")
    faults += [f"{position}: extra {got['end']}" for position, got in answer.items()
               if position not in rules and got["currency"] == reporting]
    return not faults, faults


def run(out: Path) -> dict:
    client = Client(out / "cache")
    tables = []
    for symbol, accession, end in PILOT + CONTROLS:
        filing = filing_for(accession)
        main = next(item for item in filing.attachments if str(item.document_type).upper().startswith("20-F"))
        content = main.content.decode("utf-8", "replace") if isinstance(main.content, bytes) else str(main.content)
        for kind, group in find_statements(parse_html(content)):
            if statement_scale(group) is None or not group[0].header:
                continue
            rules = rule_map(kind, group, end)
            if not rules or not any(abs((rule["end"] - end).days) <= 7 for rule in rules.values()):
                continue  # not one of the filing's own statements (a note's table, a summary)
            reporting = next((rule["currency"] for rule in rules.values() if not rule["translation"]), None)
            record = {"symbol": symbol, "accession": accession, "kind": kind, "set": "pilot" if (symbol, accession, end) in PILOT else "control"}
            try:
                answer = parse_columns(client.ask(SYSTEM, column_prompt(kind, group), symbol=symbol), group[0])
                record["agrees"], record["faults"] = agrees(rules, answer, reporting)
            except (AnswerError, RuntimeError) as error:
                record["agrees"], record["faults"] = False, [f"answer: {error}"]
            tables.append(record)
    summary = {"tables": len(tables), "agree": sum(item["agrees"] for item in tables), "calls": client.calls,
               "cached": client.cached, "cost_cny": round(client.cost, 4), "seconds": round(client.seconds, 1),
               "model": "deepseek-flash", "thinking": False}
    (out / "pilot.json").write_text(json.dumps({"summary": summary, "tables": tables}, indent=1, default=str))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1] if len(sys.argv) > 1 else "pilot-out")), indent=1))
