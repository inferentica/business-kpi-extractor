"""The currency of a statement that names none, or only its kind ("$", "pesos"): from its own filing, else the company's
reporting currency of that kind that year."""
from __future__ import annotations

from collections import defaultdict

from ..domain.basis import BARE_DOLLARS, BARE_PESOS, same_kind
from ..selection.choose import _reporting_currencies


def settle_currencies(readings) -> None:
    _filing_currencies(readings)
    _bare_currencies(readings)
    _sibling_currencies(readings)


def _filing_currencies(readings) -> None:
    """A printed statement that states no currency is in the currency its own filing's XBRL gives the same statement
    (Brookfield's 40-Fs: their tags are in U.S. dollars, their printed pages name none)."""
    tagged = {(reading.accession, reading.statement): reading.currency for reading in readings
              if reading.source == "xbrl" and reading.currency}
    for reading in readings:
        if reading.source == "xbrl":
            continue
        own = tagged.get((reading.accession, reading.statement))
        if reading.currency is None or reading.currency in (BARE_DOLLARS, BARE_PESOS) and same_kind(reading.currency, own):
            reading.currency = own or reading.currency


def _bare_currencies(readings) -> None:
    """A statement printing only "$" or "pesos" is in the company's own reporting currency of that kind that year
    (Alcon's "($ millions)" in US dollars, Scotiabank's in Canadian dollars, GAP's "thousands of pesos" in Mexican
    pesos); a year whose annual reports give no single one of that kind leaves it without."""
    by_year = _reporting_currencies(readings)
    marked = [(reading, reading.currency) for reading in readings if reading.currency in (BARE_DOLLARS, BARE_PESOS)]
    for reading, marker in marked:
        kinds = [currency for currency in by_year.get(reading.fiscal_year, set()) if same_kind(marker, currency)]
        reading.currency = kinds[0] if len(kinds) == 1 else None
    # A statement's columns share its one "$": Brookfield's Q2 2001 release prints 2001 beside 2000, and 2000 is in
    # Canadian dollars (its 2002 annual report, the one presenting it), so 2001 is too, though 2003's report in US
    # dollars leaves the year itself undecided.
    decided: dict[tuple, set] = defaultdict(set)
    for reading, marker in marked:
        if reading.currency:
            decided[(reading.accession, reading.statement, marker)].add(reading.currency)
    for reading, marker in marked:
        found = decided.get((reading.accession, reading.statement, marker), set())
        if reading.currency is None and len(found) == 1:
            reading.currency = next(iter(found))


def _sibling_currencies(readings) -> None:
    """A printed statement that names no currency is in the one its filing's other statements name, when they name a
    single one the company reports in that year (Pampa's 2008 quarterly reports: the balance sheet in reais, the
    income statement and cash flows naming none)."""
    by_year = _reporting_currencies(readings)
    named: dict[str, set] = defaultdict(set)
    for reading in readings:
        if reading.source != "xbrl" and reading.currency:
            named[reading.accession].add(reading.currency)
    for reading in readings:
        found = named.get(reading.accession, set())
        if reading.currency is None and reading.source != "xbrl" and len(found) == 1 and \
                next(iter(found)) in by_year.get(reading.fiscal_year, found):
            reading.currency = next(iter(found))
