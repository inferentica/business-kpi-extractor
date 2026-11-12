"""A statement table's columns as DeepSeek reads them: for each figure position, the period it covers and the currency
it is printed in. The prompt shows the title, the header rows and the first rows with figures, each cell at its
position; the answer names positions only from the table and is checked against it."""
from __future__ import annotations

import json
import re
from datetime import date

from ..readers.printed.tables import Table, figure_columns

PROMPT_VERSION = "columns-v1"
SYSTEM = (
    "You read the column headings of one financial statement table from an SEC filing. Each figure column has a "
    "position number. For every figure position, say which period it covers and which currency its figures are in, "
    "from the table's own words only (title, headings, currency signs beside figures, the title's definitions such as "
    "'U.S. dollars ($)' and 'Mexican pesos (Ps.)'). A balance sheet column is a date (months 0); an income or cash flow "
    "column covers months ending on a date (12 for a year, 3 for a quarter, 6, 9). A column that is not a period of the "
    "statement (a note number, a percentage, a change) is left out. Answer with JSON only: "
    '{"columns": [{"position": 7, "period_end": "2011-12-31", "months": 12, "currency": "MXN"}]} '
    "with ISO 4217 currency codes. Never give a figure."
)


def column_prompt(kind: str, group: list[Table], rows: int = 10) -> str:
    """The table as the model sees it: its title, then each header and figure row as position: text cells."""
    table = group[0]
    lines = [f"Statement: {kind}", f"Text before the table: {table.context[-400:]}"]
    for cells in table.header:
        lines.append("Header | " + " | ".join(f"{cell.start}: {cell.text.strip()}" for cell in cells if cell.text.strip()))
    shown = 0
    for row in table.rows:
        cells = [f"{cell.start}: {cell.text.strip()}" for cell in row.cells if cell.text.strip()]
        lines.append(("Row | " if row.figures else "Text | ") + " | ".join(cells))
        shown += bool(row.figures)
        if shown >= rows:
            break
    lines.append("Figure positions: " + ", ".join(str(position) for column in figure_columns(table) for position in column))
    return "\n".join(lines)


class AnswerError(ValueError):
    pass


def parse_columns(text: str, table: Table) -> dict[int, dict]:
    """The answer's columns by position, each {"end": date, "months": int, "currency": str}; only positions the table
    prints figures at, real dates, months 0-12 and three-letter currency codes."""
    positions = {position for column in figure_columns(table) for position in column}
    try:
        data = json.loads(re.sub(r"^```[a-z]*\s*|\s*```$", "", text.strip()))
        found = {}
        for item in data["columns"]:
            position, months, currency = int(item["position"]), int(item["months"]), str(item["currency"]).upper()
            end = date.fromisoformat(str(item["period_end"]))
            if position not in positions or not 0 <= months <= 12 or not re.fullmatch(r"[A-Z]{3}", currency):
                raise AnswerError(f"column {item} is not one of the table's")
            found[position] = {"end": end, "months": months, "currency": currency}
        return found
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise AnswerError(str(error)[:300]) from error


# DeepSeek's own CNY list per 1M tokens at peak (as the KPI proxy prices it, _shared/deepseek-config.ts), half off-peak.
PRICE_CNY = {"hit": 0.04, "miss": 2.0, "output": 8.0}


def cost_cny(usage: dict, peak: bool) -> float:
    hit = usage.get("prompt_cache_hit_tokens", 0)
    miss = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0) - hit)
    cost = (hit * PRICE_CNY["hit"] + miss * PRICE_CNY["miss"] + usage.get("completion_tokens", 0) * PRICE_CNY["output"]) / 1e6
    return cost if peak else cost / 2
