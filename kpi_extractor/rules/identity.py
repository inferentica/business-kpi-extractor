"""Identity rules: which row is a KPI's (row labels and their footnote marks, a listed KPI's name, a place the table
never names, a row a table names twice)."""

from __future__ import annotations

import re

from ..ai import GroupSpec, KpiSpec
from ..document import clean, parse_number, Table
from ..xbrl import member_key
from .table import _heading, _section_heading


def _identity(text: str) -> str:
    """A label or key reduced to what it names: "3-nanometer", "3nm" and "nm3" are one node; "Net product sales" and
    "net_product_sales" one line."""
    key = member_key(text.replace("_", " "))
    key = re.sub(r"nanometers?", "nm", key)
    return re.sub(r"^nm(\d+)$", r"\1nm", key)


def _listed_kpi(group: GroupSpec, label: str) -> KpiSpec | None:
    """The KPI on the company's list a table row is, so a whole-table reading and a row-by-row reading of one table use
    one key (both readings then agree, and a breakdown is never stored twice under two names)."""
    wanted = _identity(label)
    listed = [kpi for kpi in group.kpis if kpi.key != group.total_kpi]
    matches = [kpi for kpi in listed if wanted in (_identity(kpi.label), _identity(kpi.key))]
    if not matches:
        # A row label that adds the business's name to the listed one ("UnitedHealthcare Employer & Individual -
        # Domestic" for "Employer & Individual - Domestic"): its ending, when that is specific and names one KPI.
        # Not after a joining word: "Markets & Securities Services" is a combined line, not Securities Services.
        matches = [kpi for kpi in listed if len(_identity(kpi.label)) >= 8 and wanted.endswith(_identity(kpi.label))
                   and not re.search(r"(?:&|\band|\+|,|/)\s*$", label[:len(label) - len(_tail(label, kpi.label))], re.I)]
    if not matches and (bare := _SPACED_FOOTNOTE.sub("", label)) != label:
        # A footnote mark set apart from the label ("Net system sales 3", ASML's 2017 releases) when the label without
        # it is a listed KPI: never a name's own number ("Tier 1"), which would have no listed KPI to match.
        return _listed_kpi(group, bare)
    return matches[0] if len(matches) == 1 else None


_SPACED_FOOTNOTE = re.compile(r"\s+\d{1,2}$")


_TOTAL_LABEL = re.compile(r"\s*total\b", re.I)


def _combined_line(row_label: str, kpi_label: str) -> bool:
    """A row that ends with a KPI's name but adds a total or a joined part ("Total Markets & Securities Services" for
    Securities Services): a subtotal that contains the KPI, not the KPI."""
    tail = _tail(row_label, kpi_label)
    if not tail or _identity(row_label) == _identity(kpi_label):
        return False
    prefix = row_label[:len(row_label) - len(tail)]
    return bool(re.search(r"(?:&|\band|\+|,|/)\s*$", prefix, re.I) or re.match(r"\s*total\b", row_label, re.I))


def _tail(label: str, listed: str) -> str:
    """The end of a row label that spells the listed name, with the same letters and digits ("Employer & Individual -
    Domestic" at the end of "UnitedHealthcare Employer & Individual - Domestic")."""
    wanted = _identity(listed)
    for start in range(len(label)):
        if _identity(label[start:]) == wanted:
            return label[start:]
    return ""


def row_key(label: str) -> str:
    """A table row's KPI key: its label normalized the same way across filings ("3-nanometer" → "r3nanometer")."""
    key = member_key(label)
    return (key if key[:1].isalpha() else f"r{key}")[:48] or "row"


ROW_NAMED_TWICE = "the table names this row twice, with another figure"


_SUB_ROW = re.compile(r"^\s*[\-\u2013\u2022\u00b7*]\s*\w")


_TWIN_REACH = 8


def _plain_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^[\s\-\u2013\u2022\u00b7*]+", "", label)).strip().lower()


def _row_named_twice(table: Table, row: int, col: int) -> str | None:
    """Another row of the table with the read row's own name and a different figure in its column, if any: Auna lists
    "- Holding and eliminations" under Colombia (-1) and under its Peru segments (-101), and the group's is both."""
    if row >= len(table.rows) or col >= len(table.rows[row]):
        return None
    label = _row_label(table.rows[row], col)
    name, value = _plain_label(label), parse_number(table.rows[row][col])
    # Only a sub-row ("- Holding and eliminations") under one parent beside its twin under another: a release that
    # repeats its segment names section by section (revenue, then EBITDA) names each row once per section.
    if not name or value is None or not _SUB_ROW.match(label):
        return None
    for index in range(max(0, row - _TWIN_REACH), min(len(table.rows), row + _TWIN_REACH + 1)):
        other = table.rows[index]
        if index == row or col >= len(other) or not _SUB_ROW.match(twin := _row_label(other, col)) or _plain_label(twin) != name:
            continue
        figure = parse_number(other[col])
        if figure is not None and figure.value != value.value:
            return f"row {index} gives {other[col].strip()}"
    return None


def _row_label(row: list[str], col: int) -> str:
    """A row's label: the first cell with words to the left of the value (not a "$ -" filler in another column), without
    a footnote marker run into it ("Banking & Wealth Management14", "devices(1)")."""
    label = next((cell for cell in row[:col] if re.search(r"[A-Za-z]", cell)),
                 next((cell for cell in row if re.search(r"[A-Za-z]", cell) and not re.search(r"\d", cell)), ""))
    return _FOOTNOTE.sub("", label).strip()


_FOOTNOTE = re.compile(r"(?:(?<=[a-z]{3})\d{1,2}|\s*\(\d{1,2}\)|\s*\([a-z]\))$")


def _label_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _FOOTNOTE.sub("", clean(text or "")).lower())


def labels_agree(named: str | None, actual: str) -> bool:
    """Whether the row label a reader names is the row's own: the same words once case, punctuation and footnote marks
    are set aside, one label inside the other ("Wafer" in "Wafer revenue"), or at least half the words shared. A
    reader that names no label, or a row without one, has nothing to check."""
    named_words, actual_words = _label_words(named or ""), _label_words(actual)
    if not named_words or not actual_words:
        return True
    a, b = "".join(named_words), "".join(actual_words)
    if a in b or b in a:
        return True
    shared = set(named_words) & set(actual_words)
    return len(shared) >= 0.5 * min(len(set(named_words)), len(set(actual_words)))


# A metric whose name gives a place ("Japan Policyholder Account Balances") read from a row that names no such place,
# under no heading or caption that does (Prudential's "International Businesses" total): held for review.
PLACE_NOT_NAMED = "the row and its headings name no"


_PLACE = re.compile(r"\b(Japan|China|U\.?S\.?|United States|North America|Latin America|Americas|Europe|EMEA|APAC|"
                    r"Asia Pacific|Asia|Canada|Mexico|Brazil|Germany|U\.?K\.?|United Kingdom|India|Korea|Taiwan|France|"
                    r"Australia|Africa|Middle East|Peru|Colombia|Chile|Türkiye|Turkey|Spain|Italy)(?![A-Za-z])", re.I)


# A footnote mark may follow a place: Vodafone's row "Other Europe1" names Europe.
# A place a name leaves out ("Excluding Intra-Europe") is not the figure's own.
_NOT_THE_PLACE = re.compile(r"(?:excluding|excl\.?|ex|intra|non|outside|other than)[- ]\s*$", re.I)


def _places(text: str, own: bool = False) -> set[str]:
    return {match.group(0).replace(".", "").upper() for match in _PLACE.finditer(text)
            if not (own and _NOT_THE_PLACE.search(text[:match.start()][-16:]))}


def _place_not_named(label: str, table: Table, row: int, col: int) -> str | None:
    """The place a metric's name gives that its row, its section headings, its column and its table's caption all
    leave unnamed, if any."""
    places = _places(label, own=True)
    if not places:
        return None
    around = " ".join([" ".join(table.rows[row]) if row < len(table.rows) else "", _section_heading(table, row),
                       " ".join(_heading(header, col) for header in table.rows[:row][:6] if col < len(header)),
                       table.context[-200:]])
    named = _places(around)
    missing = places - named
    return ", ".join(sorted(missing)) if missing else None
