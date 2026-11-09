"""A plain-text line as a label and the figures that end it: words one space apart are one chunk, two or more spaces
part chunks, and a run of figures closes the line."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..printed.tables import NOTE_MARK, clean

CHUNK = re.compile(r"\S+(?: \S+)*")  # words one space apart; two or more spaces part chunks
FIGURE = re.compile(r"^\(?-?\d[\d,]*(\.\d+)?\)?$|^\(?-?\.\d+\)?$|^-+$|^—$")
PERCENT = re.compile(r"^\(?-?[\d.,]+\)?%$")
RULE = re.compile(r"^[\s\-=_]*$")
YEAR = re.compile(r"^(19|20)\d{2}$")
# A scale caption, which a footnote mark can follow; a statement's line can end in "(2)", a figure in brackets.
_CAPTION_WORDS = re.compile(r"\b(in\s+(thousands?|millions?|billions?)|expressed|figures|amounts\s+in)\b", re.I)
_MONTH_END = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?$", re.I)


@dataclass
class Chunk:
    start: int
    end: int
    text: str


@dataclass
class Line:
    indent: int
    label: str
    figures: list[Chunk]


def chunks(line: str) -> list[Chunk]:
    return [Chunk(match.start(), match.end(), match.group(0)) for match in CHUNK.finditer(line)]


def figures_apart(found: list[Chunk]) -> list[Chunk]:
    """A chunk of figures one space apart is a figure each: Apple's 1994 "(257,228) (1,359,796)"."""
    out = []
    for chunk in found:
        parts = list(re.finditer(r"\S+", chunk.text))
        if len(parts) > 1 and all(FIGURE.match(part.group(0)) for part in parts):
            out.extend(Chunk(chunk.start + part.start(), chunk.start + part.end(), part.group(0)) for part in parts)
        else:
            out.append(chunk)
    return out


def split(line: str) -> Line | None:
    """A line's label and the run of figures that ends it ("Net sales  $ 9,188,748  $ 7,976,954")."""
    parts = figures_apart(chunks(line.replace("$", " ")))
    for part in parts:  # a footnote mark on a figure: ASML's "(21,836)(3)"
        bare = re.sub(r"(?<=[\d)])\(\d{1,2}\)$", "", part.text)
        if bare != part.text and FIGURE.match(bare):
            part.text, part.end = bare, part.end - (len(part.text) - len(bare))
    if not parts:
        return None
    run = len(parts)
    while run > 0 and (FIGURE.match(parts[run - 1].text) or PERCENT.match(parts[run - 1].text)):
        run -= 1
    label_chunks, figures = parts[:run], [chunk for chunk in parts[run:] if not PERCENT.match(chunk.text)]
    if label_chunks and figures and _MONTH_END.search(label_chunks[-1].text) and re.fullmatch(r"\d{1,2},?", figures[0].text):
        label_chunks, figures = label_chunks + figures[:1], figures[1:]  # "December   31" and the years after it
    if label_chunks and figures and all(re.fullmatch(r"\(\d\)", chunk.text) for chunk in figures) and \
            _CAPTION_WORDS.search(" ".join(chunk.text for chunk in label_chunks)):
        label_chunks, figures = parts, []  # footnote marks after a caption: "(Expressed in Millions …)  (1)"
    if len(label_chunks) > 1 and re.fullmatch(r"\d{1,2}", label_chunks[0].text):
        label_chunks = label_chunks[1:]  # a note number in the margin: ASML's "3       Total cost of sales"
    label = " ".join(chunk.text for chunk in label_chunks)
    label = NOTE_MARK.sub("", re.sub(r"\s*\.{2,}[\s.]*$", "", label)).strip()  # dot leaders: "Net sales........"
    return Line(label_chunks[0].start if label_chunks else parts[0].start, clean(label), figures)

