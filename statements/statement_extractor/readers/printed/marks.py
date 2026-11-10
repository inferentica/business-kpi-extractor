"""Words of a laid-out page that are footnote marks, not text: shared by the readers that place words by position
(PDF pages, HTML pages of positioned text)."""
from __future__ import annotations

import statistics


def without_marks(words: list[dict]) -> list[dict]:
    """Superscript marks: one or two characters set much smaller than the page's text."""
    sizes = [word["size"] for word in words if len(word["text"]) >= 3]
    if not sizes:
        return words
    usual = statistics.median(sizes)
    return [word for word in words if not (len(word["text"]) <= 2 and word["size"] < usual * 0.8)]
