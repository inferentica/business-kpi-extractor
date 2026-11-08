"""Currency rules: a figure's currency from its marks, its column's heading or the document's stated currency."""

from __future__ import annotations

from ..ai import KpiSpec
from ..document import _DECLARED_CURRENCIES, declared_currency, Table
from .table import _heading


_CURRENCY_MARKERS = (("NT$", "TWD"), ("US$", "USD"), ("HK$", "HKD"), ("C$", "CAD"), ("A$", "AUD"), ("RMB", "CNY"),
                     ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("S/", "PEN"), ("R$", "BRL"), ("$", "USD"))


def _currency(text: str | None, kpi: KpiSpec, default_currency: str | None, context: str = "") -> str | None:
    """A value's currency, most specific evidence first: a marker on the value itself (NT$, US$, €), then what its
    table or passage declares, then the company's reporting currency. A bare "$" is only a dollar sign: in TSMC's
    reports it means New Taiwan dollars, so it defers to the declaration and the reporting currency."""
    if kpi.unit not in ("currency", "ratio"):
        return None
    for marker, code in _CURRENCY_MARKERS:
        if marker != "$" and text and marker in text:
            return code
    declared = declared_currency(context)
    if declared:
        return declared
    if text and "$" in text:
        return default_currency or "USD"
    return default_currency if kpi.unit == "currency" else None


def _column_currency(table: Table, row: int, col: int, text: str | None) -> str | None:
    """The one currency a row label or a column's own header names, when its table gives each figure in two: Auna's
    "2Q'25 (USD)" beside "2Q'25" under "(Figures in millions of Soles and millions of US Dollars)". A marker on the value
    comes first."""
    if text and any(marker != "$" and marker in text for marker, _ in _CURRENCY_MARKERS):
        return None
    # A row label's own currency, Axia's "Average ACL Price (R$/MWh)".
    label = table.rows[row][0] if 0 <= row < len(table.rows) and table.rows[row] else ""
    if marked := next((code for marker, code in _CURRENCY_MARKERS if marker != "$" and marker in label), None):
        return marked
    cells = " ".join(_heading(header, col) for header in table.rows[:max(row, 1)][:6] if col < len(header))
    found = {code for pattern, code in _DECLARED_CURRENCIES if pattern.search(cells)}
    return found.pop() if len(found) == 1 else None
