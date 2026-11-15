"""The anchor-phrase rule, the first learned rule (ops/learned.py): a quoted figure found again by the words that stood
just before it last quarter, when the sentence around them was worded anew ("cRPO were 67 percent" one quarter, "cRPO
were 65%" the next). Of 592 consecutive quotes worded anew, 251 kept the three words before the figure and 101 more
kept two. Three words are tried first, then two; the phrase must lead to exactly one figure in the whole document, and
the reading then passes every check a replay must."""
from __future__ import annotations

import re

from ..ai import Locator
from ..documents.document import Document, clean, parse_number

KIND = "anchor-phrase"
_WORD = re.compile(r"[A-Za-z]+")
# Words that say how a figure is stated, not what it is: an anchor names its metric by its other words.
_STATING = {"of", "was", "were", "is", "are", "the", "a", "an", "at", "to", "in", "on", "for", "by", "and", "with", "our",
            "its", "their", "this", "that", "there", "over", "about", "approximately", "nearly", "quarter", "year", "period",
            "end", "reached", "totaled", "totalled", "stood", "rose", "grew", "increased", "decreased", "declined", "fell",
            "appreciated", "up", "down", "from", "compared", "versus", "vs", "ended", "ending", "as"}
_SCALED = re.compile(r"\b(?:trillion|billion|million|thousand|bn|mn)\b", re.I)


def names_metric(words: tuple[str, ...], label: str) -> bool:
    """Whether an anchor's words name the metric: at least two of them, every one a word of the metric's label (or the
    label's initials: "nim" for Net Interest Margin). "off ratio of" names Net Charge-off Ratio; "assets of" (Wells Fargo,
    the two words left of "client assets of") names nothing, and found brokerage assets; "ltm gross return" names none
    of KKR's three strategies it stood before, and one phrase read all three."""
    label_words = [word.lower() for word in _WORD.findall(label or "")]
    known = set(label_words) | {"".join(word[0] for word in label_words)}
    content = [word for word in words if word not in _STATING]
    return len(content) >= 2 and all(word in known for word in content)


def rounded_elsewhere(document: Document, value: float, value_text: str) -> bool:
    """Whether a sentence's rounded figure ("$32 billion") is printed more precisely in one of the filing's tables
    (31,835 in millions): the table's figure is the one to read, so the rule leaves it to replay or the AI."""
    if not _SCALED.search(value_text or "") or not value:
        return False
    whole = str(int(round(abs(value))))
    step = 10 ** (len(whole) - len(whole.rstrip("0")))  # the figure's last stated place: $32 billion, to the billion
    if step <= 1:
        return False
    for table in document.tables.values():
        for row in table.rows:
            for cell in row[1:]:
                number = parse_number(cell)
                if number is None or not number.value:
                    continue
                for scale in (1e3, 1e6, 1e9, 1e12):
                    precise = abs(number.value) * scale
                    if precise != abs(value) and round(precise / step) * step == abs(value) and precise % step:
                        return True
    return False
# A figure as releases write it: an optional currency, the number, an optional scale or percent.
_FIGURE = (r"(?:US\$|NT\$|[$€£¥])?\s?\(?(?:\d(?:[\d,]*\d)?(?:\.\d+)?|\.\d+)\)?"
           r"(?:\s?(?:%|percent|trillion|billion|million|thousand|bn|mn)\b|%)?")


def anchor_words(quote: str, value_text: str, count: int) -> tuple[str, ...] | None:
    """The last words before a quote's figure, or None when there are fewer, or the figure is not once in the quote."""
    quote, value_text = clean(quote), clean(value_text)
    at = quote.find(value_text)
    if not value_text or at < 0 or quote.count(value_text) > 1:
        return None
    words = _WORD.findall(quote[:at])
    return tuple(word.lower() for word in words[-count:]) if len(words) >= count else None


def anchor_locator(document: Document, key: str, last: dict, label: str | None = None) -> tuple[Locator, str] | str:
    """This quarter's figure for a KPI quoted last quarter, found by the words before it, with the phrase that found it;
    or why there is none. With the metric's label, the phrase must name the metric (names_metric)."""
    quote, value_text = last.get("quote") or "", last.get("value_text") or ""
    why = "no words before the figure"
    for count in (3, 2):
        words = anchor_words(quote, value_text, count)
        if not words:
            continue
        if label is not None and not names_metric(words, label):
            why = "anchor phrase does not name the metric"
            continue
        pattern = re.compile(r"\b" + r"[^A-Za-z0-9]{1,6}".join(map(re.escape, words)) + r"(?:[^A-Za-z0-9$€£¥(.]|\.(?!\d)){0,4}(" + _FIGURE + ")",
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
