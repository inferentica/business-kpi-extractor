"""HTML laid out as pages of positioned text, read as the plain-text reader reads a PDF's pages.

Some filing agents convert a printed report into HTML page by page: each line of text is a <div> placed by its left
and top in pixels, gaps are spacer <div>s of a given width, and there is no <table> anywhere (UBS's quarterly reports
since 2023, made with Certent CDM). The table reader sees nothing in them. Each text run here becomes words at their
places on the page, as pdfplumber gives a PDF's words, and the PDF layout puts them on a character grid.
"""
from __future__ import annotations

import html
import re

from ..printed.marks import without_marks

_PAGE = re.compile(r'<div\s+id="Page\d+"[^>]*position:\s*relative', re.I)
_RUN = re.compile(r'<div[^>]*?style="([^"]*position:\s*absolute[^"]*)"[^>]*>(.*?)</div>(?=\s*(?:<div[^>]*position:\s*absolute|'
                  r'</div>|$))', re.I | re.S)
_SPACER = re.compile(r'<div[^>]*display:\s*inline-block;\s*width:\s*([\d.]+)px[^>]*>.*?</div>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_FIGURE = re.compile(r"^\(?-?\$?[\d,]*\.?\d+\)?%?$|^[—–-]$")
_BODY_SIZE = re.compile(r"<body[^>]*font-size:\s*([\d.]+)px", re.I)
# An average glyph of the condensed faces these reports set their tables in, as a share of the font size; figures
# are placed by their right edge, which this estimate gives, since the page records only where a run starts.
_GLYPH = 0.5


def is_positioned(content: str) -> bool:
    """Pages of absolutely placed text and no tables."""
    head = content[:400_000].lower()
    return len(_PAGE.findall(content[:2_000_000])) >= 3 and head.count("position:absolute") > 300 and "<tr" not in head


def _style(style: str, name: str) -> float | None:
    match = re.search(rf"(?<![-\w]){name}:\s*([\d.]+)px", style)
    return float(match.group(1)) if match else None


def _run(style: str, inner: str, default_size: float) -> dict | None:
    """A run: where it starts, its size, and its pieces of text and spacer widths in order."""
    left, top = _style(style, "left"), _style(style, "top")
    if left is None or top is None:
        return None
    pieces = []
    for piece in re.split(r"(<div[^>]*display:\s*inline-block[^>]*>.*?</div>)", inner, flags=re.I | re.S):
        spacer = _SPACER.match(piece)
        if spacer:
            pieces.append(float(spacer.group(1)))
        else:
            text = html.unescape(_TAG.sub("", piece)).replace("\xa0", " ")
            if text:
                pieces.append(text)
    return {"left": left, "top": top, "size": _style(style, "font-size") or default_size, "pieces": pieces}


def _phrases(run: dict) -> list[dict]:
    """A run's phrases at their places, split where a spacer is wider than a couple of letters (a page number after its
    title); a narrower spacer is a space between words. Each phrase's right edge is estimated from its letters, so a
    figure can be placed by it, as printed."""
    glyph = run["size"] * _GLYPH
    phrases, x, current, spaced = [], run["left"], None, False
    for piece in run["pieces"]:
        if isinstance(piece, float):
            if piece > glyph * 2:
                current = None
            spaced = True
            x += piece
            continue
        for match in re.finditer(r"\S+(?:\s\S+)*", piece):
            text, start = match.group(0), x + match.start() * glyph
            if current is not None and match.start() == 0 and not spaced:
                current["text"] += text
            elif current is not None and match.start() <= 1:
                current["text"] += " " + text
            else:
                current = {"text": text, "x0": start, "top": run["top"], "size": run["size"]}
                phrases.append(current)
            current["x1"] = start + len(text) * glyph
            spaced = False
        spaced = spaced or piece.endswith(" ")
        x += len(piece) * glyph
    return phrases


def _line(phrases: list[dict], unit: float) -> list[str]:
    """One printed line as text on the character grid; a label too long for the room before the next phrase wraps,
    its head on a line of its own above, as printed statements wrap labels."""
    out, above = "", []
    for index, phrase in enumerate(phrases):
        text = phrase["text"]
        figure = bool(_FIGURE.match(text))
        start = round(phrase["x1"] / unit) - len(text) if figure else round(phrase["x0"] / unit)
        start = max(start, len(out) + 2) if out else start
        following = phrases[index + 1] if index + 1 < len(phrases) else None
        if following is not None and not figure:
            room = (round(following["x1"] / unit) - len(following["text"]) if _FIGURE.match(following["text"])
                    else round(following["x0"] / unit)) - 2 - start
            words = text.split(" ")
            tail = []
            while words and len(" ".join(words)) > room and len(words) > 1:
                tail.insert(0, words.pop())
            if tail:
                above.append(" " * start + " ".join(words))
                text = " ".join(tail)
        out = out.ljust(start) + text
    return above + [out]


def positioned_text(content: str) -> str:
    """Every page's lines, pages apart by a form feed, as pdf_text gives a PDF's."""
    body = _BODY_SIZE.search(content[:5000])
    default_size = float(body.group(1)) if body else 10.0
    unit = default_size * _GLYPH
    starts = [match.start() for match in _PAGE.finditer(content)] + [len(content)]
    pages = []
    for start, end in zip(starts, starts[1:]):
        page = content[start:end]
        height = _style(page[:600], "height") or 1123.0
        runs = [run for style, inner in _RUN.findall(page) if (run := _run(style, inner, default_size))]
        phrases = without_marks([phrase for run in runs for phrase in _phrases(run)])
        lines: list[list[dict]] = []
        for phrase in sorted(phrases, key=lambda item: (item["top"], item["x0"])):
            if lines and abs(phrase["top"] - lines[-1][0]["top"]) <= 2.5:
                lines[-1].append(phrase)
            else:
                lines.append([phrase])
        # The running footer names the section its page belongs to ("Consolidated financial statements | UBS Group AG
        # interim consolidated financial statements (unaudited) 56"): set first, it is the context of the page's
        # statement, whose own title says only "Balance sheet".
        footer = [line for line in lines if line[0]["top"] > height * 0.93]
        lines = footer + [line for line in lines if line[0]["top"] <= height * 0.93]
        pages.append("\n".join(text for line in lines for text in _line(sorted(line, key=lambda item: item["x0"]), unit)))
    # A page break ends a statement, as <PAGE> does in a plain-text filing: each of these reports prints its statements
    # a page each, and a statement printed over two says so ("(continued)").
    return "\n<PAGE>\n".join(pages)
