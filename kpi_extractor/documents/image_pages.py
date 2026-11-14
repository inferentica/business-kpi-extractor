"""Exhibits whose pages are only pictures (Ambev's, Vale's, Petrobras' and Bradesco's decks: image pages with no text
beneath them) read by two OCR models, keeping only the figures both read alike.

DeepSeek's OCR (through the control plane) gives each page's text and tables with the region each block holds; the
local model (RapidOCR, as the chart reader uses it) gives the words with their boxes. A figure DeepSeek reads in a
block stands only where RapidOCR read the same digits inside that block's region; every other figure is replaced by a
mark no reader parses as a number. Neither model alone is trusted with a figure: in the probe (evals/ocr_probe.json)
each misread digits the other did not (DeepSeek ASE's 21,932,480 as ...490; RapidOCR Bradesco's 1,574 as 1.574), and on
tables the figures both read alike were all right. Charts are left out: DeepSeek gives no words inside them."""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Callable
from urllib.parse import urljoin

UNCONFIRMED = "[unconfirmed]"
# A figure as printed: "1,148", "(357)", "-30", "12.5%", "$0.93", "R$3.8". Digits glued to letters ("3Q26", "FY25")
# are labels, never figures.
_FIGURE = re.compile(r"(?<![A-Za-z\d.,])\d{1,3}(?:[,.]\d{3})+(?:[.,]\d+)?(?![A-Za-z\d])|(?<![A-Za-z\d.,])\d+(?:[.,]\d+)?(?![A-Za-z\d])")
_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']?([^\"' >]+)[^>]*>", re.I)
_BLOCK = re.compile(r"<｜ref｜>(\w+)<｜/ref｜><｜box｜>\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]<｜/box｜>")
_TOKEN = re.compile(r"<｜[^｜]*｜>")
# A picture is a page when the text after it, up to the next picture, gives fewer figures than this: ASML's and Wells
# Fargo's slides keep their words beneath each picture, and a chart set in a release (Coca-Cola FEMSA's) sits among its
# paragraphs. An exhibit is read as pictures from two such pages that are most of its pictures: ASML's and TSMC's decks,
# their slides worded, have picture-only dividers and pie slides that the chart reader reads by their picture and its
# hidden title, and are left as they are.
_MAX_FIGURES_BENEATH = 3
_MIN_PAGES = 2
# A block's region is widened by this share of the page on each side: the two models draw their boxes differently.
_MARGIN = 15


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)))


def picture_pages(raw_html: str, url: str) -> list[tuple[str, str]]:
    """The pictures of an exhibit that are its pages, with no words of their own beneath them (Ambev's deck: 34 page
    pictures under a 6-K cover whose dates and file numbers are its only figures), each as its tag and absolute link."""
    found = list(_IMG.finditer(raw_html))
    pages = []
    for index, match in enumerate(found):
        beneath = raw_html[match.end():found[index + 1].start() if index + 1 < len(found) else len(raw_html)]
        if len(_FIGURE.findall(_text(beneath))) < _MAX_FIGURES_BENEATH:
            pages.append((match.group(0), urljoin(url, match.group(1))))
    return pages if len(pages) >= _MIN_PAGES and len(pages) * 2 > len(found) else []


def _digits(figure: str) -> str:
    return re.sub(r"\D", "", figure)


def _alike(ours: str, theirs: str) -> bool:
    """Two readings of one figure: the same digits, and the same separators, or a comma and a point swapped only
    where three digits follow (a thousands group, which RapidOCR reads either way)."""
    if _digits(ours) != _digits(theirs) or len(ours) != len(theirs):
        return False
    for index, (a, b) in enumerate(zip(ours, theirs)):
        if a != b and ({a, b} != {",", "."} or not re.match(r"\d{3}(?!\d)", ours[index + 1:])):
            return False
    return True


def _confirm(text: str, seen: Counter) -> tuple[str, int, int]:
    """The text with each figure kept where `seen` (the other model's figures in the region) has it, else masked."""
    kept = masked = 0

    def check(match: re.Match) -> str:
        nonlocal kept, masked
        figure = match.group(0)
        other = next((theirs for theirs in seen if seen[theirs] > 0 and _alike(figure, theirs)), None)
        if other is None:
            masked += 1
            return UNCONFIRMED
        seen[other] -= 1
        kept += 1
        # Swapped separators: the reading whose separators agree with each other ("19,901,959,514", not DeepSeek's
        # "19,901,959.514"); DeepSeek's when both do, as it swapped them far less often in the probe.
        return other if len(set(re.sub(r"\d", "", figure))) > 1 and len(set(re.sub(r"\d", "", other))) <= 1 else figure
    # Only text outside tags is checked: a table's markup never holds a figure.
    parts = re.split(r"(<[^>]+>)", text)
    return "".join(part if part.startswith("<") else _FIGURE.sub(check, part) for part in parts), kept, masked


def agreed_page(deepseek: str, words: list[tuple[float, float, float, float, str]], size: tuple[int, int]) -> tuple[str, int, int]:
    """One page as HTML from DeepSeek's reading, each figure kept only where RapidOCR read it in the same region (the
    whole page when DeepSeek gave no regions). Returns the HTML and the figures kept and masked."""
    width, height = size
    blocks = []
    matches = list(_BLOCK.finditer(deepseek))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(deepseek)
        kind, box = match.group(1), tuple(int(value) for value in match.groups()[1:])
        blocks.append((kind, box, deepseek[match.end():end].strip()))
    if not matches:
        blocks = [("text", (0, 0, 1000, 1000), _TOKEN.sub(" ", deepseek).strip())]
    out, kept, masked = [], 0, 0
    for kind, (x0, y0, x1, y1), content in blocks:
        if kind == "image" or not content:
            continue  # a chart or a picture: DeepSeek reads no words in it
        region = (max(0, x0 - _MARGIN) * width / 1000, max(0, y0 - _MARGIN) * height / 1000,
                  min(1000, x1 + _MARGIN) * width / 1000, min(1000, y1 + _MARGIN) * height / 1000)
        seen = Counter(figure for wx0, wy0, wx1, wy1, text in words
                       if region[0] <= (wx0 + wx1) / 2 <= region[2] and region[1] <= (wy0 + wy1) / 2 <= region[3]
                       for figure in _FIGURE.findall(text))
        content = _TOKEN.sub(" ", content)
        if "<table" not in content:
            content = f"<p>{html.escape(re.sub(r'^#+ ', '', content))}</p>"
        confirmed, block_kept, block_masked = _confirm(content, seen)
        out.append(confirmed)
        kept += block_kept
        masked += block_masked
    return "\n".join(out), kept, masked


def read_image_pages(sources: list[str], fetch: Callable[[str], bytes], deepseek: Callable[[str, bytes], tuple[str, bool]],
                     local: Callable[[bytes], list], size_of: Callable[[bytes], tuple[int, int]],
                     min_side: int = 400) -> tuple[dict[str, str], dict]:
    """Each page picture as HTML of agreed figures (by its link), and what was kept: `deepseek` reads a page (its text,
    and whether it gave regions), `local` gives RapidOCR's words. A logo, an icon, or a page DeepSeek returns empty is
    left out."""
    pages, totals = {}, Counter()
    for number, source in enumerate(sources, 1):
        data = fetch(source)
        if min(size_of(data)) < min_side:
            continue  # a logo, an icon or a bullet, not a page
        text, _regions = deepseek(source.rsplit("/", 1)[-1], data)
        if not text.strip():
            totals["empty pages"] += 1
            continue
        words = [(w.x0, w.y0, w.x1, w.y1, w.text) for w in local(data)]
        page, kept, masked = agreed_page(text, words, size_of(data))
        totals["pages"] += 1
        totals["figures kept"] += kept
        totals["figures masked"] += masked
        pages[source] = f'<div class="ocr-page" data-page="{number}" data-image="{html.escape(source)}">{page}</div>'
    return pages, dict(totals)
