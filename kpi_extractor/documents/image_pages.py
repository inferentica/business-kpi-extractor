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

# A figure as printed, whole: "1,148", "(357)", "-30", "12.5%", "$0.93", "R$3.8", with its scale glued on ("R$1,574m",
# "12bn", "R$3,8bi"). Digits glued to other letters ("3Q26", "FY25", "1H25", "4T25") are labels, never figures. A figure is
# never part of one: "1,574m" once matched as its leading "1", so masking a "1" left ",574m" (574 million) and
# "R$1,514m" agreed with "R$1,574m".
_UNIT = r"(?i:thousands?|millions?|billions?|trillions?|mm|mn|bn|bi|mil|mi|tn|m|b|k|t|x)"
_FIGURE = re.compile(r"(?<![A-Za-z\d.,])\d+(?:[.,]\d+)*(?:" + _UNIT + r")?(?![A-Za-z\d]|[.,]\d)")
# Thousands groups a model split with a space ("1 574"): masked whole, so neither group is left as a figure.
_SPACED = re.compile(r"(?<![A-Za-z\d.,])\d{1,3}(?:[ \u00a0\u2009\u202f]\d{3})+(?:[.,]\d+)?(?:" + _UNIT
                     + r")?(?![A-Za-z\d]|[.,]\d)")
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


def _marks(text: str, match: re.Match) -> tuple[bool, bool]:
    """A figure's sign and percent as printed around it: negative when a minus or an opening bracket comes just
    before it ("(357)", "-30", "−4.1%"), a percent when "%" follows ("12.5%", "12.5 %")."""
    before, after = text[:match.start()].rstrip()[-1:], text[match.end():].lstrip()[:1]
    return before in ("(", "-", "−", "–"), after == "%"


def _figures(text: str) -> list[tuple[str, bool, bool]]:
    """Each figure of a text with its sign and percent."""
    return [(match.group(0), *_marks(text, match)) for match in _FIGURE.finditer(text)]


def _alike(ours: str, theirs: str, thousands: str | None = None) -> bool:
    """Two readings of one figure: the same digits, and the same separators, or a comma and a point swapped only
    where three digits follow (a thousands group, which RapidOCR reads either way). A swap is one figure only when
    something says which mark groups thousands: the figure's other separators ("19,901,959,514" beside
    "19,901,959.514"), or the page's own (`thousands`, _thousands_mark). Otherwise "1,574" and "1.574" are two
    figures, one a thousand times the other."""
    # A scale glued on or set apart is one figure: RapidOCR's "$2billion" is DeepSeek's "$2 billion".
    ours, theirs = (re.sub(r"[A-Za-z]+$", "", figure) for figure in (ours, theirs))
    if _digits(ours) != _digits(theirs) or len(ours) != len(theirs):
        return False
    for index, (a, b) in enumerate(zip(ours, theirs)):
        if a != b and ({a, b} != {",", "."} or not re.match(r"\d{3}(?!\d)", ours[index + 1:])
                       or (len(re.findall(r"[.,]", ours)) < 2 and thousands is None)):
            return False
    return True


# A page's own decimals and long groupings say which mark separates thousands on it: "12.5%" and "19,901,959" a comma,
# Bradesco's "12,5%" and "1.574.320" a point.
_DECIMAL = {".": re.compile(r"(?<![\d.,])\d+\.\d{1,2}(?![\d.,])"), ",": re.compile(r"(?<![\d.,])\d+,\d{1,2}(?![\d.,])")}
_GROUPED = {",": re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3}){2,}(?![\d,])|\d,\d{3}\.\d"),
            ".": re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3}){2,}(?![\d.])|\d\.\d{3},\d")}


def _thousands_mark(text: str) -> str | None:
    """The mark that groups thousands on a page, when its figures say so clearly (twice as often as the other)."""
    comma = len(_DECIMAL["."].findall(text)) + len(_GROUPED[","].findall(text))
    point = len(_DECIMAL[","].findall(text)) + len(_GROUPED["."].findall(text))
    if comma and comma >= 2 * point:
        return ","
    if point and point >= 2 * comma:
        return "."
    return None


def _confirm(text: str, seen: Counter, single: dict[str, str], thousands: str | None = None) -> tuple[str, int]:
    """The text with each figure that `seen` (the other model's figures in the region, with their signs and percent
    signs) also has, sign and percent sign alike: agreed, in the rendering whose separators agree. Every other figure
    stays as DeepSeek read it and is listed in `single`."""
    kept = 0

    def checked(part: str) -> str:
        def check(match: re.Match) -> str:
            nonlocal kept
            figure, (negative, percent) = match.group(0), _marks(part, match)
            # The same digits with another sign or no percent sign are another figure: "(357)" is not "357".
            other = next((theirs for theirs in seen if seen[theirs] > 0 and _alike(figure, theirs[0], thousands)
                          and theirs[1:] == (negative, percent)), None)
            if other is None:
                single.setdefault(_digits(figure), DEEPSEEK)
                return figure
            seen[other] -= 1
            kept += 1
            # Swapped separators: the reading whose separators agree with each other ("19,901,959,514", not DeepSeek's
            # "19,901,959.514"); with one separator, the reading in the page's own (Bradesco's "1.574" where RapidOCR
            # read "1,574"); DeepSeek's when both do, as it swapped them far less often in the probe.
            marks, their_marks = (re.sub(r"[\dA-Za-z]", "", reading) for reading in (figure, other[0]))
            if len(marks) == 1 and thousands and {marks, their_marks} == {",", "."}:
                return figure if marks == thousands else other[0]
            return other[0] if len(set(re.sub(r"\d", "", figure))) > 1 and len(set(re.sub(r"\d", "", other[0]))) <= 1 else figure
        return _FIGURE.sub(check, part)
    # Only text outside tags is checked: a table's markup never holds a figure. A cell's sign is the cell's own.
    parts = re.split(r"(<[^>]+>)", text)
    return "".join(part if part.startswith("<") else checked(part) for part in parts), kept


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
    thousands = _thousands_mark(_TOKEN.sub(" ", deepseek))
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
        inside = [w for w in words if region[0] <= (w[0] + w[2]) / 2 <= region[2] and region[1] <= (w[1] + w[3]) / 2 <= region[3]]
        seen = Counter(figure for line in _lines(inside) for figure in _figures(line))
        content = _TOKEN.sub(" ", content)
        if "<table" not in content:
            content = f"<p>{html.escape(re.sub(r'^#+ ', '', content))}</p>"
        confirmed, block_kept = _confirm(content, seen, single, thousands)
        out.append(confirmed)
        kept += block_kept
    return "\n".join(out), kept, single


def read_image_pages(sources: list[str], fetch: Callable[[str], bytes], deepseek: Callable[[str, bytes], tuple[str, bool]],
                     local: Callable[[bytes], list], size_of: Callable[[bytes], tuple[int, int]],
                     min_side: int = 400, workers: int = _WORKERS) -> tuple[dict[str, str], dict[str, str], dict]:
    """Each page picture as HTML (by its link), the figures one model alone read across them (digits -> the model), and
    what was read: `deepseek` reads a page (its text, and whether it gave regions), `local` gives RapidOCR's words. A
    logo or an icon is left out, and so is a page that could not be fetched or recognized, counted in "pages not read":
    one broken link once failed the whole release, three attempts and then given up. Pages are read a few at a time
    (a 33-page deck took five minutes one by one)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    one_at_a_time = threading.Lock()  # the local model is one shared instance, and works the CPU either way

    def read(numbered: tuple[int, str]):
        number, source = numbered
        try:
            data = fetch(source)
            if min(size_of(data)) < min_side:
                return source, None, 0, {}, False  # a logo, an icon or a bullet, not a page
            text, _regions = deepseek(source.rsplit("/", 1)[-1], data)
            with one_at_a_time:
                words = [(w.x0, w.y0, w.x1, w.y1, w.text) for w in local(data)]
        except Exception:  # noqa: BLE001 - a broken link or one failed recognition costs that page, not the release
            return source, None, 0, {}, None
        page, kept, single = agreed_page(text, words, size_of(data))
        tag = f'<div class="ocr-page" data-page="{number}" data-image="{html.escape(source)}">{page}</div>'
        return source, tag, kept, single, not text.strip()

    pages, single_reads, totals = {}, {}, Counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for source, page, kept, single, empty in pool.map(read, enumerate(sources, 1)):
            if page is None:
                totals["pages not read"] += int(empty is None)  # left out: no figure on it reaches any reader
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


UNREAD = "[?]"  # a figure one OCR model alone read, as the code readers see it: no figure at all


def agreed_only(document):
    """The document as code readers see it: each figure one OCR model alone read is masked, so no reader without the
    hold (release statements, quarter tables, quoted rows, release breakdowns and columns) can store it. The AI's
    reading sees it and holds it (hold_single_reads). The same document when its pages had no single reads."""
    from .document import Document, Table, TextBlock

    single = getattr(document, "single_reads", None) or {}
    if not single:
        return document

    def masked_spaced(match: re.Match) -> str:
        parts = _FIGURE.findall(match.group(0))
        return UNREAD if _digits(match.group(0)) in single or any(_digits(part) in single for part in parts) else match.group(0)

    def mask(text: str) -> str:
        text = _SPACED.sub(masked_spaced, text or "")
        return _FIGURE.sub(lambda match: UNREAD if _digits(match.group(0)) in single else match.group(0), text)

    masked = Document(source_url=document.source_url, order=list(document.order), part_urls=dict(document.part_urls))
    masked.tables = {key: Table(id=table.id, context=mask(table.context), rows=[[mask(cell) for cell in row] for row in table.rows])
                     for key, table in document.tables.items()}
    masked.blocks = {key: TextBlock(id=block.id, text=mask(block.text)) for key, block in document.blocks.items()}
    return masked
