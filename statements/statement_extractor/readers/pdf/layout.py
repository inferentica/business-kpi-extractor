"""A PDF report's pages as plain text laid out by the words' own positions, for the plain-text reader.

Archived reports from before EDGAR held foreign filers' documents (TSMC's 20-Fs and quarterly statements, ASML's
annual reports) are PDFs with a text layer. Their figures line up on the page by their right edges to the point; a text
export by character counts does not keep that. So each word is placed on a grid of the page's typical character width:
a figure by its right edge, any other word by its left edge, and words a gap apart stay at least two spaces apart, as
the plain-text reader expects of separate columns.
"""
from __future__ import annotations

import io
import re
import statistics

_FIGURE = re.compile(r"^\(?-?\$?[\d,]*\.?\d+\)?%?$|^[—–-]$")
_LINE_SLACK = 2.5  # points two words' tops can differ by on one printed line


def pdf_text(content: bytes) -> str:
    """Every page's lines, pages apart by a form feed. One character width for the whole document, so a statement's
    columns stay in place from one page to the next."""
    import pdfplumber  # only reports from archives need it
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        # Words split where the font size changes, so a superscript footnote mark is a word of its own
        # ("Operations¹", "2001²" in ASML's FY2001 20-F) and can be left out.
        pages = [_without_marks(page.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"]))
                 for page in pdf.pages]
    widths = [(word["x1"] - word["x0"]) / len(word["text"]) for words in pages for word in words if len(word["text"]) >= 3]
    unit = statistics.median(widths) if widths else 5.0
    return "\n\f\n".join(_page_text(words, unit) for words in pages)  # a break a statement can run on across


def _without_marks(words: list[dict]) -> list[dict]:
    """Superscript marks: one or two characters set much smaller than the page's text."""
    sizes = [word["size"] for word in words if len(word["text"]) >= 3]
    if not sizes:
        return words
    usual = statistics.median(sizes)
    return [word for word in words if not (len(word["text"]) <= 2 and word["size"] < usual * 0.8)]


def _page_text(words: list[dict], unit: float) -> str:
    if not words:
        return ""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_SLACK:
            lines[-1].append(word)
        else:
            lines.append([word])
    return "\n".join(_line_text(sorted(line, key=lambda item: item["x0"]), unit) for line in lines)


# A page number standing alone at the foot of a page: "- 4 -", "F-4", "12".
_PAGE_NUMBER = re.compile(r"^\s*[-–]?\s*(?:[A-Z]-?)?\s*\d{1,3}\s*[-–]?\s*$")


def _brackets(words: list[dict]) -> list[dict]:
    """A bracket set apart from its figure ("(          44.0)"), joined to it."""
    joined: list[dict] = []
    for word in words:
        if joined and joined[-1]["text"] == "(" and _FIGURE.match(word["text"] if word["text"].startswith("(") else "(" + word["text"]):
            opening = joined.pop()
            word = {**word, "text": "(" + word["text"].lstrip("("), "x0": opening["x0"]}
        elif joined and word["text"] == ")" and _FIGURE.match(joined[-1]["text"] + ")"):
            joined[-1] = {**joined[-1], "text": joined[-1]["text"] + ")", "x1": word["x1"]}
            continue
        joined.append(word)
    return joined


def _glyphs(text: str) -> str:
    """A character the font names only by its code ("(cid:146)"): its Windows-1252 character, as the report's font
    encodes quotes and dashes (’ – —)."""
    def character(match: re.Match) -> str:
        code = int(match.group(1))
        try:
            return bytes([code]).decode("cp1252") if 128 <= code <= 255 else ""
        except UnicodeDecodeError:
            return ""
    return re.sub(r"\(cid:(\d+)\)", character, text)


def _line_text(words: list[dict], unit: float) -> str:
    text, previous = "", None
    words = _brackets([{**word, "text": _glyphs(word["text"])} for word in words if _glyphs(word["text"])])
    if _PAGE_NUMBER.match(" ".join(word["text"] for word in words)):
        return ""
    for word in words:
        figure = bool(_FIGURE.match(word["text"]))
        start = round(word["x1"] / unit) - len(word["text"]) if figure else round(word["x0"] / unit)
        if previous is not None:
            # A gap of a few spaces is another column; a narrower one is a space inside a phrase, however widely a
            # heading spaces its words ("December   31,").
            apart = figure or word["x0"] - previous["x1"] > unit * 2.5  # a figure keeps its place by its right edge
            start = max(start, len(text) + 2) if apart else len(text) + 1
        text = text.ljust(start) + word["text"]
        previous = word
    return text
