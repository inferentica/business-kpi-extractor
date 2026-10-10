"""Breakdowns a company shows only as charts: ASML's "Net system sales breakdown" slide gives technology, end-use and
region as pie charts whose labels and percentages are part of the picture, so no text of the filing carries them.

The slide is read with a local OCR model (no AI service), and the words are put back into charts by where they stand:
a column per chart title, a row per quarter. A pie is kept only when its shares add up to a whole."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

_SLIDE = re.compile(r"<img[^>]+src=\"([^\"]+\.(?:jpe?g|png))\"[^>]*>\s*<div[^>]*>\s*<font[^>]*>(.*?)</font>", re.I | re.S)
_BREAKDOWN = re.compile(r"\bbreakdown\b", re.I)
# "Q2 2026" on newer slides, "Q1'26" on older ones.
_QUARTER = re.compile(r"^Q([1-4])[’'`]?((?:19|20)?\d{2})$")
_SHARE = re.compile(r"^(?P<label>.*?)\s?(?P<share>\d{1,3})\s?%$")
# Shares are printed as whole percentages: six of them can be off by a point each way in total.
_WHOLE = (97.5, 102.5)

# The picture's lettering loses its spaces ("SouthKorea") and an I can read as a 1 ("1-line").
_NAMES = {
    "euv": "EUV", "arfi": "ArFi", "arfdry": "ArF Dry", "krf": "KrF", "iline": "I-line", "1line": "I-line",
    "metrologyinspection": "Metrology & Inspection", "logic": "Logic", "memory": "Memory",
    "southkorea": "South Korea", "korea": "South Korea", "taiwan": "Taiwan", "china": "China", "usa": "USA", "japan": "Japan",
    "restofasia": "Rest of Asia", "emea": "EMEA", "europe": "Europe", "singapore": "Singapore",
    "restofworld": "Rest of World",
}
_TITLES = {
    "technology": "technology", "enduse": "end-use", "regionshiptolocation": "region", "region": "region",
}


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class ChartBreakdown:
    """One pie: what it splits ("Net system sales"), by what ("technology"), for which quarter, and its shares."""
    subject: str
    axis: str
    fiscal_year: str
    fiscal_period: str
    shares: dict[str, float] = field(default_factory=dict)


@dataclass
class ChartUnits:
    """One bar chart of units sold by technology for a quarter ("Sales in lithography units")."""
    fiscal_year: str
    fiscal_period: str
    units: dict[str, int] = field(default_factory=dict)
    # A technology whose count the picture did not give up (a one-digit figure over a sliver of a bar): the caller
    # may fill it from the quarter's reported total, or leave the chart out.
    missing: str | None = None


def breakdown_slides(raw_html: str) -> list[tuple[str, str]]:
    """(image, title text) of each slide whose hidden text calls it a breakdown."""
    slides = []
    for source, text in _SLIDE.findall(raw_html or ""):
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()
        if _BREAKDOWN.search(text):
            slides.append((source, text))
    return slides


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _name(label: str) -> str:
    known = _NAMES.get(_squash(label))
    if known:
        return known
    # Unknown lettering: put back the spaces lost before capitals ("NorthAmerica").
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label).replace("&", " & ").replace("  ", " ").strip()


def read_breakdowns(words: list[Word]) -> list[ChartBreakdown]:
    """The pies of a breakdown slide. Columns come from the chart titles, rows from the quarter labels at the left;
    a share is a "Label NN%" (the label may sit on the line above, or wrap onto two)."""
    return [chart for chart in _read(words) if isinstance(chart, ChartBreakdown)]


def read_units(words: list[Word]) -> list[ChartUnits]:
    """The slide's bar charts of units sold: each bar's count stands over it and its technology under it."""
    return [chart for chart in _read(words) if isinstance(chart, ChartUnits)]


def _read(words: list[Word]) -> list[ChartBreakdown | ChartUnits]:
    # The picture's lettering can lose its spaces ("Net systemsalesbreakdown"), so no word boundary is asked for.
    title = next((word for word in sorted(words, key=lambda item: item.y0) if "breakdown" in word.text.lower()), None)
    quarters = sorted((word for word in words if _QUARTER.match(word.text.replace(" ", ""))), key=lambda item: item.cy)
    if title is None or not quarters:
        return []
    subject = re.sub(r"\s*breakdown.*$", "", title.text, flags=re.I).strip() or "Sales"
    subject = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", subject)
    subject = "Net system sales" if _squash(subject) == "netsystemsales" else subject
    first_row = min(word.y0 for word in quarters)
    titles = sorted((word for word in words if _squash(word.text) in _TITLES and title.y1 < word.cy < first_row + 5),
                    key=lambda item: item.cx)
    if not titles:
        return []
    # A column runs from halfway to the chart on its left to halfway to the next title of any kind on its right.
    band = sum(word.cy for word in titles) / len(titles)
    headers = sorted((word for word in words if abs(word.cy - band) <= 14 and word.x0 > quarters[0].x1),
                     key=lambda item: item.cx)
    edges = [(left.x1 + right.x0) / 2 for left, right in zip(headers, headers[1:])]
    bounds = {id(header): (edges[index - 1] if index else quarters[0].x1, edges[index] if index < len(edges) else float("inf"))
              for index, header in enumerate(headers)}
    row_edges = [(upper.cy + lower.cy) / 2 for upper, lower in zip(quarters, quarters[1:])]
    charts: list[ChartBreakdown | ChartUnits] = []
    unit_header = next((header for header in headers if _squash(header.text).startswith("salesinlithography")), None)
    for row, quarter in enumerate(quarters):
        top = titles[0].y1 if row == 0 else row_edges[row - 1]
        bottom = row_edges[row] if row < len(row_edges) else float("inf")
        period = _QUARTER.match(quarter.text.replace(" ", ""))
        for header in titles:
            left, right = bounds[id(header)]
            cell = [word for word in words if left <= word.cx < right and top <= word.cy < bottom and word is not header]
            shares = _shares(cell)
            if len(shares) >= 2 and _WHOLE[0] <= sum(shares.values()) <= _WHOLE[1]:
                year = period.group(2) if len(period.group(2)) == 4 else f"20{period.group(2)}"
                charts.append(ChartBreakdown(subject, _TITLES[_squash(header.text)], year, f"Q{period.group(1)}", shares))

    if unit_header is not None:
        # Bars stand lower than the pies beside them, so their rows are told by the lines of technology names under
        # them, top to bottom, one line per quarter.
        left, right = bounds[id(unit_header)]
        column = [word for word in words if left <= word.cx < right and word.cy > unit_header.y1 + 30]
        baselines: list[float] = []
        for word in sorted((word for word in column if _squash(word.text) in _NAMES), key=lambda item: item.cy):
            if not baselines or word.cy - baselines[-1] > 40:
                baselines.append(word.cy)
        if len(baselines) == len(quarters):
            for row, (quarter, baseline) in enumerate(zip(quarters, baselines)):
                above = baselines[row - 1] + 12 if row else unit_header.y1 + 30
                units, missing = _units([word for word in column if above <= word.cy <= baseline + 12])
                period = _QUARTER.match(quarter.text.replace(" ", ""))
                if units:
                    year = period.group(2) if len(period.group(2)) == 4 else f"20{period.group(2)}"
                    charts.append(ChartUnits(year, f"Q{period.group(1)}", units, missing))
    return charts


def _shares(cell: list[Word]) -> dict[str, float]:
    shares: dict[str, float] = {}
    used: set[int] = set()
    for word in sorted(cell, key=lambda item: (item.y0, item.x0)):
        match = _SHARE.match(word.text.strip())
        if not match:
            continue
        label = match.group("label").strip()
        # The line above belongs to this share when it stands right over it: the whole label ("China" over "14%") or
        # its first half ("Metrology &" over "Inspection 3%").
        above = [other for other in cell if id(other) not in used and other is not word and not _SHARE.match(other.text.strip())
                 and -8 <= word.y0 - other.y1 <= 0.9 * (word.y1 - word.y0) + 6
                 and abs(other.x0 - word.x0) <= 14 and not re.fullmatch(r"[\d.,]+", other.text.strip())]
        if above and (not label or above[-1].text.rstrip().endswith("&")):
            upper = max(above, key=lambda item: item.y1)
            used.add(id(upper))
            label = f"{upper.text.strip()} {label}".strip()
        if not label:
            continue
        shares[_name(label)] = float(match.group("share"))
    return shares


def _units(cell: list[Word]) -> tuple[dict[str, int], str | None]:
    """Bars read bottom-up: the technologies stand on one line under the bars, and each bar's count is the number
    nearest above its own label. At most one label may be left without a count (named as missing)."""
    labels = [word for word in cell if _squash(word.text) in _NAMES]
    if len(labels) < 3:
        return {}, None
    baseline = max(word.cy for word in labels)
    labels = sorted((word for word in labels if abs(word.cy - baseline) <= 10), key=lambda item: item.cx)
    numbers = [word for word in cell if re.fullmatch(r"\d{1,4}", word.text.strip()) and word.cy < baseline - 10]
    units: dict[str, int] = {}
    missing: list[str] = []
    taken: set[int] = set()
    for label in labels:
        width = max(30.0, label.x1 - label.x0)
        over = [number for number in numbers if id(number) not in taken and abs(number.cx - label.cx) <= 0.6 * width]
        if not over:
            missing.append(_name(label.text))
            continue
        count = max(over, key=lambda item: item.cy)  # the lowest number over the label is the bar's own
        taken.add(id(count))
        units[_name(label.text)] = int(count.text.strip())
    if len(missing) > 1 or len(taken) != len(numbers):
        return {}, None  # more than one bar unread, or a number that belongs to no bar
    return units, (missing[0] if missing else None)


def ocr_words(image: bytes) -> list[Word]:
    """The words of a picture with their boxes, by a local OCR model."""
    from rapidocr_onnxruntime import RapidOCR  # heavy; loaded only when a chart slide is read

    result, _ = _engine(RapidOCR)(image)
    words = []
    for box, text, _score in result or []:
        xs, ys = [point[0] for point in box], [point[1] for point in box]
        words.append(Word(min(xs), min(ys), max(xs), max(ys), str(text)))
    return words


_ENGINE = None


def _engine(factory):
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = factory()
    return _ENGINE
