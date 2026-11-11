"""A table row quoted as one line of text: a label, then its figures ("Total management fees 522 577 620 18.8% 1,027
1,197 16.6%"). Slide decks reach us as text (Apollo's and KKR's releases, ASML's statements), so the AI quotes their
rows as sentences, and replay matched them as sentences: a sign that flips ("(6.3)%" to "6.5%") or a footnote mark
that changes ("Outflows2" to "Outflows3") lost the row. Here a quoted row is found again by its label and its place
among the same number of figures, and only where the filing holds that row once."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..ai import Locator
from ..documents.document import Document, clean, parse_number

# One figure of a row: a number with its sign, parentheses, currency and percent marks, or a dash for none.
_SLOT = r"(?:\$\s?)?\(?[-+]?\$?\d[\d,]*(?:\.\d+)?\)?%?x?|[—–-]{1,2}%?"
_SLOT_ONLY = re.compile(rf"^(?:{_SLOT})$")
_FOOTNOTE = re.compile(r"(?<=[A-Za-z)])\d{1,2}(?:,\d{1,2})*$")


@dataclass
class RowLine:
    label: str           # the label without its footnote marks
    slots: list[str]     # the row's figures, as written
    index: int           # which of them the reading took


def row_line(quote: str, value_text: str) -> RowLine | None:
    """The quote as a row: its label and figures, when it is one (two figures or more after a label with letters) and
    the value read is one of the figures, written once."""
    tokens = clean(quote).split(" ")
    slots: list[str] = []
    while tokens and _SLOT_ONLY.match(tokens[-1]):
        slots.insert(0, tokens.pop())
    label = _FOOTNOTE.sub("", " ".join(tokens)).strip()
    value = clean(value_text)
    if len(slots) < 2 or not re.search(r"[A-Za-z]{2}", label) or sum(1 for slot in slots if value in slot) != 1:
        return None
    index = next(i for i, slot in enumerate(slots) if value in slot)
    return RowLine(label, slots, index)


def replay_row_line(document: Document, key: str, locator: dict) -> Locator | str:
    """Last quarter's quoted row read again in this filing: the one row with its label and as many figures, the figure
    in the same place. A label held by several rows of that shape (a quarter's and a year to date's) is not read."""
    row = row_line(locator.get("quote") or "", locator.get("value_text") or "")
    if row is None:
        return "sentence not found"
    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(row.label).replace(r'\ ', r'\s+')}(?:\d{{1,2}}(?:,\d{{1,2}})*)?"
                         rf"((?:\s+(?:{_SLOT})(?=\s|$)){{{len(row.slots)}}})(?!\s+(?:{_SLOT})(?=\s|$))")
    found = [(block.id, match) for block in document.blocks.values() for match in pattern.finditer(block.text)]
    if len(found) != 1:
        return "row found more than once" if found else "sentence not found"
    block_id, match = found[0]
    slots = re.findall(rf"(?:{_SLOT})(?=\s|$)", match.group(1).strip())  # "$ 302" is one figure
    value_text = slots[row.index] if row.index < len(slots) else ""
    if parse_number(value_text) is None:
        return "sentence not found"  # the row gives no figure in that place this quarter
    return Locator(kpi=key, block=block_id, quote=match.group(0).strip(), value_text=value_text)


def row_line_others(quote: str, value_text: str) -> list[float]:
    """The other figures of a quoted row, in the row's own units (the year-ago figure among them), not its percentages."""
    row = row_line(quote, value_text)
    if row is None:
        return []
    return [parsed.value for i, slot in enumerate(row.slots) if i != row.index and not slot.endswith("%")
            and (parsed := parse_number(slot)) is not None]
