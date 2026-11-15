"""A table headed for one business segment holds that segment's figures. BNY's supplement repeats the company's row
labels ("Average loans", "Average deposits", "AUC/A at period end") on its Securities Services page; read there, the
company's average loans were served as about $11 billion (the segment's) for about $69 billion, and every check passed,
because the segment's own columns agree with each other. Where the filing prints the label in a table of the company's
too, a company-wide metric is read there, never from the segment's page. Many metrics are printed on their segment's
page alone (JPMorgan's active mobile customers, under Consumer & Community Banking): those are read where they are. A
metric whose own label (or its group's) names the segment is that segment's."""
from __future__ import annotations

import re

from ..documents.document import Table, parse_number
from .numbers import same_figure

# Reconciling lines the segment tag lists beside the segments: no page is headed for them alone.
_NOT_SEGMENTS = {"other", "corporate", "eliminations", "reconciling items", "segment reconciling items",
                 "corporate non segment", "intersegment eliminations", "all other", "corporate and other"}
# "SECURITIES SERVICES BUSINESS SEGMENT", "Wealth Management segment": a heading that says it is one segment's.
_SEGMENT_WORD = re.compile(r"\b(?:business\s+)?segment\b(?!s)", re.I)


def _words(text: str) -> str:
    """Lower-case words only, "&" as "and": "Consumer & Community Banking" and "CONSUMER AND COMMUNITY BANKING" match."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("&", " and ")))


def _label_key(label: str) -> str:
    """A row label without its parenthesised notes and footnote marks: BNY's company row "AUC/A at period end (in
    trillions) (current period is preliminary)" and its segment row "AUC/A at period end (in trillions) (b)" are one."""
    return _words(re.sub(r"\([^)]*\)", " ", label or ""))


def segment_names(labels) -> list[str]:
    """The company's segments, from its XBRL segment labels, without the reconciling lines."""
    names = {_words(label) for label in labels if label}
    return sorted(name for name in names if name and name not in _NOT_SEGMENTS)


def company_segments(values) -> list[str]:
    """segment_names of the segments a company's XBRL tags (its stored "segments" group)."""
    return segment_names(value.get("kpi_label") for value in values
                         if value.get("group_key") == "segments" and value.get("method") == "xbrl")


def _heading_line(context: str) -> str:
    """The heading printed just above a table: the text after the last sentence before it, when short (BNY's release
    puts "SECURITIES SERVICES BUSINESS SEGMENT HIGHLIGHTS" above the table, not in it)."""
    tail = re.split(r"[.;:)]\s", (context or "")[-200:])[-1].strip()
    letters = re.findall(r"[A-Za-z]", tail)
    # A title, set in capitals; a sentence's end that names a segment ("... at Optum Rx") is no heading.
    return tail if 0 < len(tail) <= 100 and letters and sum(c.isupper() for c in letters) >= 0.8 * len(letters) else ""


def heading(table: Table) -> str:
    """The table's title: the heading line above it and its rows above its first row with a label and a figure."""
    lines = [_heading_line(table.context)]
    for row in table.rows:
        if row and any(parse_number(cell) is not None and not re.fullmatch(r"\s*(?:19|20)\d{2}\s*", cell or "")
                       and not re.fullmatch(r"\s*[1-4]Q\d{2}\s*", cell or "") for cell in row[1:]):
            break
        lines.append(" ".join(cell for cell in row if cell))
    return " ".join(lines)


def segment_of(table: Table, names: list[str]) -> str | None:
    """The one segment a table is headed for, or None: a heading naming no segment, or several (a table of every
    segment side by side, whose columns are the segments and whose total is the company's)."""
    title = _words(heading(table))
    if not title:
        return None
    # A one-word segment ("KKR", the company's own name, as its single segment) is no heading's segment unless the
    # heading says segment.
    found = [name for name in names if (" " in name or _SEGMENT_WORD.search(title)) and re.search(rf"\b{re.escape(name)}\b", title)]
    # A longer name holds a shorter one ("investment and wealth management" holds "wealth management"): the longer counts.
    found = [name for name in found if not any(other != name and name in other for other in found)]
    if len(found) == 1:
        return found[0]
    if not found and _SEGMENT_WORD.search(heading(table)) and not re.search(r"\bsegments\b|\bby\b", title):
        # "Results by offering and segment" lists every segment; "Wealth Management segment" is one.
        # The words before "segment" name it; one word is the company's own ("KKR SEGMENT RESULTS": every segment's).
        named = _words(_SEGMENT_WORD.split(heading(table))[0])[-60:]
        return named if len(named.split()) >= 2 else None
    return None


def names_segment(segment: str, *labels: str) -> bool:
    """Whether a metric's own labels name the segment (its KPI or its group: "Securities Services average loans")."""
    words = set(segment.split()) - {"and", "the", "of"}
    return any(words and words <= set(_words(label).split()) for label in labels if label)


def cell_figure(table: Table, row: int, col) -> float | None:
    number = parse_number(table.cell(row, int(col))) if col is not None else None
    return number.value if number is not None else None


def segment_page_of(document, table: Table, label: str, names: list[str], figure: float | None = None) -> str | None:
    """The segment a table is headed for, when the filing also prints the row's label in a table of the company's (one
    headed for no segment) with other figures: the label is then the company's there, and the segment's here. A figure
    the company's row prints too is the company's (PNC's discretionary assets under management, on the Asset Management
    Group page and in the company's table alike). None otherwise."""
    segment = segment_of(table, names)
    if not segment:
        return None
    key = _label_key(label)
    if not key:
        return None
    rows = [row for other in document.tables.values() if other is not table and segment_of(other, names) is None
            for row in other.rows if row and _label_key(row[0]) == key]
    if not rows:
        return None
    if figure is not None and any((number := parse_number(cell)) is not None and same_figure(number.value, figure)
                                  for row in rows for cell in row[1:]):
        return None
    return segment
