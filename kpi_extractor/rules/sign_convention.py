"""A line's sign convention, as its name states it: the word outside the parentheses is positive, the one inside
negative ("Net Recoveries (Charge-offs)": recoveries positive). A row that names the same two words the other way round
("Net Charge-offs / (Recoveries)", Northern Trust's 2019 releases) prints the opposite sign of the same figure."""
from __future__ import annotations

import re

_PAIR = re.compile(r"([A-Za-z][A-Za-z-]*)\s*/?\s*\(\s*([A-Za-z][A-Za-z-]*)\s*\)")


def _word(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower()).removesuffix("s")


def _pairs(label: str) -> set[tuple[str, str]]:
    return {(_word(outside), _word(inside)) for outside, inside in _PAIR.findall(label or "")}


def opposite_convention(row_label: str, kpi_label: str) -> bool:
    """Whether the row names the line's two signed words the other way round."""
    return any((inside, outside) in _pairs(row_label) for outside, inside in _pairs(kpi_label) if outside != inside)
