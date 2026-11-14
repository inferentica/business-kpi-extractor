"""Identity rules: which row is a KPI's (row labels and their footnote marks, a listed KPI's name, a place the table
never names, a row a table names twice)."""

from __future__ import annotations

import re

from ..ai import GroupSpec, KpiSpec
from ..documents.document import clean, parse_number, Table
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
    if not matches:
        # A row naming only the side its sign falls on ("Investment securities losses", JPMorgan's quarters with a loss)
        # is the listed line that names both ("Investment Securities Gains/(Losses)").
        matches = [kpi for kpi in listed if wanted in {_identity(side) for side in _one_sided(kpi.label)}]
    if not matches and (bare := _SPACED_FOOTNOTE.sub("", label)) != label:
        # A footnote mark set apart from the label ("Net system sales 3", ASML's 2017 releases) when the label without
        # it is a listed KPI: never a name's own number ("Tier 1"), which would have no listed KPI to match.
        return _listed_kpi(group, bare)
    return matches[0] if len(matches) == 1 else None


_SIDE = re.compile(r"\s*\((?:gains?|losses?)\)", re.I)
_SIDE_WORD = re.compile(r"\b(?:gains?|losses?)\b", re.I)


def sideless_key(label: str) -> str:
    """A table row's identity from one quarter to the next, the side of a gain or loss left out: a company names the
    side its figure fell on (Wells Fargo's "Net gains (losses) from trading activities", then "Net gains from trading
    activities", "Net losses from equity securities"). Used only where no row has the exact label."""
    return member_key(_SIDE_WORD.sub("gl", _SIDE.sub("", label)))


def kept_key(group: GroupSpec, locator, label: str) -> KpiSpec | None:
    """The listed KPI a replayed table's row was last quarter, when its label alone names none (PNC's "Other")."""
    keys = getattr(locator, "_row_keys", {})
    key = keys.get(member_key(label)) or keys.get(sideless_key(label))
    return next((kpi for kpi in group.kpis if kpi.key == key and kpi.key != group.total_kpi), None)


_SPACED_FOOTNOTE = re.compile(r"\s+\d{1,2}$")
_TWO_SIDED = re.compile(r"\b(gains?|income|profits?|earnings)\s*(?:/|\band\b|\bor\b)?\s*\(?\s*(loss(?:es)?)\s*\)?", re.I)


def _one_sided(label: str) -> set[str]:
    """A label naming both sides of a figure ("Gains/(Losses)", "Income (Loss)") as each side alone."""
    found = _TWO_SIDED.search(label)
    if not found:
        return set()
    return {f"{label[:found.start()]}{side}{label[found.end():]}" for side in (found.group(1), found.group(2))}


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


# Names of one place written two ways: Coca-Cola's "EMEA Unit Case Volume Growth" is read from its "Europe, Middle East
# & Africa" row, Asia Pacific is APAC, the U.S. the United States.
_SAME_PLACE = {"APAC": "ASIA PACIFIC", "UNITED STATES": "US", "UNITED KINGDOM": "UK", "TÜRKIYE": "TURKEY"}
_EMEA = {"EUROPE", "MIDDLE EAST", "AFRICA"}


def _places(text: str, own: bool = False) -> set[str]:
    places = {_SAME_PLACE.get(name, name) for name in (match.group(0).replace(".", "").upper() for match in _PLACE.finditer(text)
              if not (own and _NOT_THE_PLACE.search(text[:match.start()][-16:])))}
    if _EMEA <= places:
        # Europe, the Middle East and Africa together are EMEA. A figure named for all three is EMEA's (its row may say
        # EMEA); a row naming all three names EMEA too, and still each one, but a row that says only EMEA never names
        # Europe alone.
        places = (places - _EMEA if own else places) | {"EMEA"}
    return places


def _place_not_named(label: str, table: Table, row: int, col: int) -> str | None:
    """The place a metric's name gives that its row, its section headings, its section's title, its column and its
    table's caption all leave unnamed, if any. The title reaches past the section's other rows: Exxon's "Natural Gas
    ($/kcf)" sits under "Crude ($/b)", both under "United States ExxonMobil"."""
    places = _places(label, own=True)
    if not places:
        return None
    title = next((text for index, text in reversed(_section_titles(table)) if index < row), "")
    around = " ".join([" ".join(table.rows[row]) if row < len(table.rows) else "", _section_heading(table, row), title,
                       " ".join(_heading(header, col) for header in table.rows[:row][:6] if col < len(header)),
                       table.context[-200:]])
    named = _places(around)
    missing = places - named
    return ", ".join(sorted(missing)) if missing else None


# A row named only as a total ("Worldwide", "Total") means its section's total: Exxon ends each section of its volume
# tables with "Worldwide" (crude production, gas production, throughput, each product's sales, capital expenditure),
# and six metrics were read from the capital expenditure table's "Worldwide" row (7,159). Such a row stands for a
# metric only under a section title that names the metric: the AI is asked again, as for any row mismatch.
MEASURE_NOT_NAMED = "row mismatch"
_AGGREGATE_ROW = re.compile(r"^(?:total )?(?:worldwide|total|consolidated|overall|global|all segments|total company)(?: total)?$")
_PERIOD_CELL = re.compile(r"(?:[1-4]q|q[1-4])\s*'?\d{2,4}|(?:fy|ytd)?\s*(?:19|20)\d{2}|ytd.*", re.I)
_PLAIN_WORDS = {"total", "worldwide", "global", "consolidated", "company", "group", "the", "of", "and", "in", "per",
                "for", "net", "day", "from"}


def _section_titles(table: Table) -> list[tuple[int, str]]:
    """Each section title of a table, with its last row: rows of words and no figures but period names ("3Q24 | 2Q24 |
    Refinery throughput, thousand barrels per day (kbd) | YTD 2024"), consecutive ones as one title (Exxon's 2021
    "Net production of crude oil, natural gas / liquids, bitumen and synthetic oil, / thousand barrels per day")."""
    titles: list[tuple[int, str]] = []
    for index, cells in enumerate(table.rows):
        figures = [cell for cell in cells if cell.strip() and parse_number(cell) is not None
                   and not _PERIOD_CELL.fullmatch(cell.strip())]
        if figures or not any(re.search(r"[A-Za-z]{3}", cell) for cell in cells):
            continue
        text = " ".join(cell for cell in cells if cell.strip())
        if titles and titles[-1][0] == index - 1:
            titles[-1] = (index, f"{titles[-1][1]} {text}")
        else:
            titles.append((index, text))
    return titles


def _measure_words(text: str) -> set[str]:
    """A label's words for naming a measure, plurals as singulars ("sales" is "sale", "products" "product")."""
    return {re.sub(r"(?<=[a-z]{3})s$", "", word) for word in _label_words(text) if len(word) > 2 and word not in _PLAIN_WORDS}


def _measure_not_named(label: str, table: Table, row: int, col: int) -> str | None:
    """Why a row named only as a total is not this metric's (another section's total), or None. Its own section's
    title must name the metric best of the table's titles (the most of its words, then the fewest others: Exxon's
    "Net production of crude oil, natural gas liquids..." names natural gas too, but its gas section names it better),
    and at least half the metric's words no other title uses ("energy" among product sales; the gas section's "available
    for sale" names no energy product)."""
    if row >= len(table.rows) or not _AGGREGATE_ROW.match(" ".join(_label_words(_row_label(table.rows[row], col)))):
        return None
    titles = _section_titles(table)
    own = next(((index, text) for index, text in reversed(titles) if index < row), None)
    wanted = _measure_words(label)
    # One title is the table's own heading: its total is the table's, nothing to tell apart (ConocoPhillips' "Total").
    if own is None or not wanted or len(titles) < 2:
        return None
    here = _measure_words(own[1])
    elsewhere = set().union(*(_measure_words(text) for index, text in titles if index != own[0]))
    # Only words some title uses: Exxon's 2021 tables say "Petroleum product sales", never "energy".
    distinctive = (wanted & (here | elsewhere)) - elsewhere
    best = max((len(wanted & _measure_words(text)), -len(_measure_words(text))) for _index, text in titles)
    if 2 * len(wanted & here) >= len(wanted) and (len(wanted & here), -len(here)) == best \
            and 2 * len(distinctive & here) >= len(distinctive):
        return None
    return (f"{MEASURE_NOT_NAMED}: {_row_label(table.rows[row], col)!r} in {table.id} r{row} is the total of "
            f"{own[1].strip()[:80]!r}, not {label}")
