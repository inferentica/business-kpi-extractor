"""A statement laid out by country or segment, one column for each: Coca-Cola Andina's releases print their income
statement as "Argentina | Brazil | Chile | Paraguay | Total" for each period. Its columns are no statement of the
group; the reader leaves the table out rather than take its first country for the company."""
from __future__ import annotations

import re

from .tables import Table

# Countries and regions a segment's column is named for (and none a statement's column is: years, quarters, currencies).
_REGIONS = re.compile(r"\b(argentina|brazil|brasil|chile|paraguay|uruguay|peru|colombia|mexico|ecuador|bolivia|venezuela|"
                      r"guatemala|panama|costa\s+rica|united\s+states|u\.s\.|canada|europe|asia|africa|china|japan|korea|"
                      r"india|spain|germany|france|italy|united\s+kingdom|philippines|egypt|latin\s+america|north\s+america|"
                      r"south\s+america|central\s+america|caribbean|middle\s+east|emea|apac|latam)\b", re.I)


def by_segment(table: Table) -> bool:
    """Whether a table's header, or the rows over its first figures, name two countries or regions or more."""
    first = next((index for index, row in enumerate(table.rows) if row.figures), 0)
    text = " ".join([" ".join(cell.text for cell in cells) for cells in table.header]
                    + [row.label for row in table.rows[:first + 1]])
    return len({re.sub(r"\s+", " ", found.lower()) for found in _REGIONS.findall(text)}) >= 2


def without_segments(found: list, notes: list) -> list:
    """The statements found, less those laid out by country or segment (noted)."""
    segmented = [kind for kind, group in found if by_segment(group[0])]
    if segmented:
        notes.append(f"by country or segment, left out: {', '.join(segmented)}")
    return [(kind, group) for kind, group in found if not by_segment(group[0])]
