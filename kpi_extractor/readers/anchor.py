"""The anchor-phrase rule, the first learned rule (ops/learned.py): a quoted figure found again by the words that stood
just before it last quarter, when the sentence around them was worded anew ("cRPO were 67 percent" one quarter, "cRPO
were 65%" the next). Of 592 consecutive quotes worded anew, 251 kept the three words before the figure and 101 more
kept two. Three words are tried first, then two; the phrase must lead to exactly one figure in the whole document, and
the reading then passes every check a replay must."""
from __future__ import annotations

import re

from ..ai import Locator
from ..documents.document import Document, clean

KIND = "anchor-phrase"
_WORD = re.compile(r"[A-Za-z]+")
# A figure as releases write it: an optional currency, the number, an optional scale or percent.
_FIGURE = (r"(?:US\$|NT\$|[$€£¥])?\s?\(?\d(?:[\d,]*\d)?(?:\.\d+)?\)?"
           r"(?:\s?(?:%|percent|trillion|billion|million|thousand|bn|mn)\b|%)?")


def anchor_words(quote: str, value_text: str, count: int) -> tuple[str, ...] | None:
    """The last words before a quote's figure, or None when there are fewer, or the figure is not once in the quote."""
    quote, value_text = clean(quote), clean(value_text)
    at = quote.find(value_text)
    if not value_text or at < 0 or quote.count(value_text) > 1:
        return None
    words = _WORD.findall(quote[:at])
    return tuple(word.lower() for word in words[-count:]) if len(words) >= count else None


def anchor_locator(document: Document, key: str, last: dict) -> tuple[Locator, str] | str:
    """This quarter's figure for a KPI quoted last quarter, found by the words before it, with the phrase that found it;
    or why there is none."""
    quote, value_text = last.get("quote") or "", last.get("value_text") or ""
    why = "no words before the figure"
    for count in (3, 2):
        words = anchor_words(quote, value_text, count)
        if not words:
            continue
        pattern = re.compile(r"\b" + r"[^A-Za-z0-9]{1,6}".join(map(re.escape, words)) + r"[^A-Za-z0-9$€£¥(]{0,4}(" + _FIGURE + ")",
                             re.I)
        found = [(block_id, match) for block_id, block in document.blocks.items() for match in pattern.finditer(block.text)]
        if len(found) == 1:
            block_id, match = found[0]
            text = document.blocks[block_id].text
            figure = match.group(1).strip()
            end = match.start(1) + len(match.group(1).rstrip())
            return Locator(kpi=key, block=block_id, quote=text[match.start():end], value_text=figure), " ".join(words)
        why = "anchor phrase leads to several figures" if found else "anchor phrase not found"
    return why
