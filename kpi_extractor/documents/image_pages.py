"""Exhibits whose pages are only pictures (Ambev's, Vale's, Petrobras' and Bradesco's decks: image pages with no text
beneath them) read by two OCR models.

DeepSeek's OCR (through the control plane) gives each page's text and tables with the region each block holds; the
local model (RapidOCR, as the chart reader uses it) gives the words with their boxes. A figure DeepSeek reads in a
block is agreed when RapidOCR read the same digits inside that block's region. Neither model alone is trusted with a
figure: in the probe (evals/ocr_probe.json) each misread digits the other did not (DeepSeek ASE's 21,932,480 as ...490;
RapidOCR Bradesco's 1,574 as 1.574), and on tables the figures both read alike were all right.

A figure one model alone read is kept in the text and listed in `single_reads`: a value read from it is held
(hold_single_reads) until a check proves its digits (the figure XBRL tags, parts adding up to a total both models
read, the next year's release printing it again). DeepSeek gives no words inside a chart or on a page it returns
empty; there RapidOCR's words stand alone, every figure in them listed."""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Callable
from urllib.parse import urljoin

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
# Pages read at once: well under DeepSeek's 15 recognitions in progress per account and SEC's request rate.
_WORKERS = 4
DEEPSEEK, LOCAL = "DeepSeek", "RapidOCR"
ONE_READER = "read by one OCR model only"
SUMS_TO_AGREED_TOTAL = "its breakdown adds up exactly to a total both OCR models read"


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


def _confirm(text: str, seen: Counter, single: dict[str, str]) -> tuple[str, int]:
    """The text with each figure that `seen` (the other model's figures in the region) also has: agreed, in the
    rendering whose separators agree. Every other figure stays as DeepSeek read it and is listed in `single`."""
    kept = 0

    def check(match: re.Match) -> str:
        nonlocal kept
        figure = match.group(0)
        other = next((theirs for theirs in seen if seen[theirs] > 0 and _alike(figure, theirs)), None)
        if other is None:
            single.setdefault(_digits(figure), DEEPSEEK)
            return figure
        seen[other] -= 1
        kept += 1
        # Swapped separators: the reading whose separators agree with each other ("19,901,959,514", not DeepSeek's
        # "19,901,959.514"); DeepSeek's when both do, as it swapped them far less often in the probe.
        return other if len(set(re.sub(r"\d", "", figure))) > 1 and len(set(re.sub(r"\d", "", other))) <= 1 else figure
    # Only text outside tags is checked: a table's markup never holds a figure.
    parts = re.split(r"(<[^>]+>)", text)
    return "".join(part if part.startswith("<") else _FIGURE.sub(check, part) for part in parts), kept


def _lines(words: list[tuple[float, float, float, float, str]]) -> list[str]:
    """RapidOCR's words as lines: by their vertical middles, then left to right."""
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        middle, height = (word[1] + word[3]) / 2, max(1.0, word[3] - word[1])
        if lines and abs((lines[-1][0][1] + lines[-1][0][3]) / 2 - middle) <= height / 2:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [" ".join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in lines]


def _local_only(words: list, single: dict[str, str]) -> str:
    """Words RapidOCR alone read (a chart's lettering, a page DeepSeek returned empty), every figure listed."""
    lines = _lines(words)
    for line in lines:
        for figure in _FIGURE.findall(line):
            single.setdefault(_digits(figure), LOCAL)
    return "".join(f"<p>{html.escape(line)}</p>" for line in lines)


def agreed_page(deepseek: str, words: list[tuple[float, float, float, float, str]],
                size: tuple[int, int]) -> tuple[str, int, dict[str, str]]:
    """One page as HTML: DeepSeek's reading with its figures checked against RapidOCR's in the same region (the whole
    page when DeepSeek gave no regions), and RapidOCR's words where DeepSeek gave none (its charts, or an empty page).
    Returns the HTML, the figures both read, and those one model alone read (digits -> the model)."""
    width, height = size
    single: dict[str, str] = {}
    matches = list(_BLOCK.finditer(deepseek))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(deepseek)
        blocks.append((match.group(1), tuple(int(value) for value in match.groups()[1:]), deepseek[match.end():end].strip()))
    if not matches and _TOKEN.sub(" ", deepseek).strip():
        blocks = [("text", (0, 0, 1000, 1000), _TOKEN.sub(" ", deepseek).strip())]
    if not blocks:
        return _local_only(words, single), 0, single
    out, kept = [], 0
    for kind, (x0, y0, x1, y1), content in blocks:
        if kind == "image":
            # A chart or a picture: DeepSeek reads no words in it, RapidOCR's stand alone (strictly inside its region).
            inside = [w for w in words if x0 * width / 1000 <= (w[0] + w[2]) / 2 <= x1 * width / 1000
                      and y0 * height / 1000 <= (w[1] + w[3]) / 2 <= y1 * height / 1000]
            out.append(_local_only(inside, single))
            continue
        if not content:
            continue
        region = (max(0, x0 - _MARGIN) * width / 1000, max(0, y0 - _MARGIN) * height / 1000,
                  min(1000, x1 + _MARGIN) * width / 1000, min(1000, y1 + _MARGIN) * height / 1000)
        seen = Counter(figure for w in words if region[0] <= (w[0] + w[2]) / 2 <= region[2]
                       and region[1] <= (w[1] + w[3]) / 2 <= region[3] for figure in _FIGURE.findall(w[4]))
        content = _TOKEN.sub(" ", content)
        if "<table" not in content:
            content = f"<p>{html.escape(re.sub(r'^#+ ', '', content))}</p>"
        confirmed, block_kept = _confirm(content, seen, single)
        out.append(confirmed)
        kept += block_kept
    return "\n".join(out), kept, single


def read_image_pages(sources: list[str], fetch: Callable[[str], bytes], deepseek: Callable[[str, bytes], tuple[str, bool]],
                     local: Callable[[bytes], list], size_of: Callable[[bytes], tuple[int, int]],
                     min_side: int = 400, workers: int = _WORKERS) -> tuple[dict[str, str], dict[str, str], dict]:
    """Each page picture as HTML (by its link), the figures one model alone read across them (digits -> the model), and
    what was read: `deepseek` reads a page (its text, and whether it gave regions), `local` gives RapidOCR's words. A
    logo or an icon is left out. Pages are read a few at a time (a 33-page deck took five minutes one by one)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    one_at_a_time = threading.Lock()  # the local model is one shared instance, and works the CPU either way

    def read(numbered: tuple[int, str]):
        number, source = numbered
        data = fetch(source)
        if min(size_of(data)) < min_side:
            return source, None, 0, {}, False  # a logo, an icon or a bullet, not a page
        text, _regions = deepseek(source.rsplit("/", 1)[-1], data)
        with one_at_a_time:
            words = [(w.x0, w.y0, w.x1, w.y1, w.text) for w in local(data)]
        page, kept, single = agreed_page(text, words, size_of(data))
        tag = f'<div class="ocr-page" data-page="{number}" data-image="{html.escape(source)}">{page}</div>'
        return source, tag, kept, single, not text.strip()

    pages, single_reads, totals = {}, {}, Counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for source, page, kept, single, empty in pool.map(read, enumerate(sources, 1)):
            if page is None:
                continue
            totals["pages"] += 1
            totals["empty pages"] += int(empty)
            totals["figures agreed"] += kept
            totals["figures one model read"] += len(single)
            for digits, model in single.items():
                single_reads.setdefault(digits, model)
            pages[source] = page
    return pages, single_reads, {key: value for key, value in totals.items() if value}


def printed_digits(item, document) -> str | None:
    """The digits of the figure a value was read from: its table cell, or the figure its quote points at."""
    locator = item.locator or {}
    table = document.tables.get(str(locator.get("table"))) if locator.get("table") is not None else None
    if table is not None and locator.get("row") is not None and locator.get("col") is not None:
        found = _FIGURE.search(table.cell(int(locator["row"]), int(locator["col"])) or "")
        return _digits(found.group(0)) if found else None
    found = _FIGURE.search(str(locator.get("value_text") or ""))
    return _digits(found.group(0)) if found else None


def hold_single_reads(read: list, document) -> None:
    """Holds each value read from a figure one OCR model alone read, naming the model, unless its breakdown adds up
    exactly (no rounding allowed: one misread digit can hide in it) to a total both models read."""
    single = getattr(document, "single_reads", None) or {}
    if not single:
        return
    groups: dict[str, list] = {}
    for item in read:
        groups.setdefault(item.group.key, []).append(item)
    for items in groups.values():
        totals = [item for item in items if item.is_total or item.kpi.key == item.group.total_kpi]
        parts = [item for item in items if item not in totals]
        agreed_total = (len(totals) == 1 and bool(parts) and printed_digits(totals[0], document) not in single
                        and abs(sum(item.value for item in parts) - totals[0].value) <= max(abs(totals[0].value) * 1e-9, 1e-6))
        for item in items:
            model = single.get(printed_digits(item, document) or "")
            if model is None:
                continue
            if agreed_total and item in parts and item.status == "verified":
                item.notes.append(f"{ONE_READER} ({model}); {SUMS_TO_AGREED_TOTAL}")
                continue
            item.status = "needs_review"
            item.notes.append(f"{ONE_READER} ({model})")
