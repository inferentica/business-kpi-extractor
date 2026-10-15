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
    # Older slides spell the immersion slice out over two lines, and either line can be the one read with its share.
    "arfimmersion": "ArFi", "immersion": "ArFi", "arf": "ArFi",
    "metrologyinspection": "Metrology & Inspection", "logic": "Logic", "memory": "Memory",
    "southkorea": "South Korea", "korea": "South Korea", "taiwan": "Taiwan", "china": "China", "usa": "USA", "japan": "Japan",
    "restofasia": "Rest of Asia", "emea": "EMEA", "europe": "Europe", "singapore": "Singapore",
    "restofworld": "Rest of World",
}
# What each pie can be split into. A share under any other name is a misread ("Koroa") and the pie is left out; a
# name that belongs to the pie beside it ("I-line" drifting into end-use) is not this pie's share.
_AXIS_NAMES = {
    "technology": {"EUV", "ArFi", "ArF Dry", "KrF", "I-line", "Metrology & Inspection"},
    "end-use": {"Logic", "Memory", "Foundry", "IDM"},
    "region": {"South Korea", "Taiwan", "China", "USA", "Japan", "Rest of Asia", "EMEA", "Europe", "Singapore", "Rest of World"},
}
# What the bars of units are named for: the lithography technologies, never a region or an end-use from the pies.
_UNIT_NAMES = {"EUV", "ArFi", "ArF Dry", "KrF", "I-line"}
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

    @property
    def exact(self) -> bool:
        """The shares add up to exactly 100, as every pie checked by eye does. One that is a point off may have lost
        or gained a slice, so it needs a second slide showing the same before it is believed."""
        return abs(sum(self.shares.values()) - 100) < 1e-9


@dataclass
class ChartUnits:
    """One bar chart of units sold by technology for a quarter ("Sales in lithography units")."""
    fiscal_year: str
    fiscal_period: str
    units: dict[str, int] = field(default_factory=dict)
    # A technology whose count the picture did not give up (a one-digit figure over a sliver of a bar): the caller
    # may fill it from the quarter's reported total, or leave the chart out.
    missing: str | None = None
    # Technologies whose count two readings of the picture agree on. A count read once may be a misread digit (an 8
    # taken for a 0), which a fill from the total would then hide.
    confirmed: frozenset[str] = frozenset()
    # Every technology under the bars, read or not (one reading's view, before readings are weighed).
    names: tuple[str, ...] = ()


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


def read_units(words: list[Word], *details: list[Word] | None) -> list[ChartUnits]:
    """The slide's bar charts of units sold: each bar's count stands over it and its technology under it. `details`
    are closer readings of the bar chart alone (see `unit_region`), which older slides need: their counts are small
    and grey, and a reading of the whole slide loses some.

    No single reading is trusted. Each bar takes the count most readings give it; where readings disagree with no
    majority the bar counts as unread. `confirmed` names the bars at least two readings agree on."""
    readings = [None, *[detail for detail in details if detail is not None]]
    seen: dict[tuple[str, str], list[dict[str, int]]] = {}
    labels: dict[tuple[str, str], list[str]] = {}
    for reading in readings:
        for chart in _read(words, reading, partial=True):
            if not isinstance(chart, ChartUnits):
                continue
            quarter = (chart.fiscal_year, chart.fiscal_period)
            seen.setdefault(quarter, []).append(chart.units)
            for name in chart.names:
                if name not in labels.setdefault(quarter, []):
                    labels[quarter].append(name)
    charts: list[ChartUnits] = []
    for quarter, reads in seen.items():
        names = labels[quarter]
        if not names:
            continue
        units: dict[str, int] = {}
        confirmed: set[str] = set()
        for name in names:
            counts = [read[name] for read in reads if name in read]
            if not counts:
                continue
            top = max(set(counts), key=counts.count)
            agreeing = counts.count(top)
            if agreeing * 2 <= len(counts) and len(set(counts)) > 1:
                continue  # readings disagree with no majority: unread
            units[name] = top
            if agreeing >= 2:
                confirmed.add(name)
        unread = [name for name in names if name not in units]
        if len(unread) > 1 or len(names) < 3:
            continue
        ordered = {name: units[name] for name in names if name in units}
        charts.append(ChartUnits(quarter[0], quarter[1], ordered, unread[0] if unread else None, frozenset(confirmed)))
    return charts


def _unit_header(words: list[Word]) -> Word | None:
    # The bar chart's heading stands level with the pie titles on newer slides and lower on older ones.
    return next((word for word in words if _squash(word.text).startswith("salesinlithography")), None)


def unit_region(words: list[Word]) -> tuple[int, int] | None:
    """Where the bar chart of units stands on the slide: the (left, top) of the part to read again, enlarged."""
    header = _unit_header(words)
    return None if header is None else (max(0, int(header.x0 - 70)), int(header.y1 + 4))


def _read(words: list[Word], detail: list[Word] | None = None, partial: bool = False) -> list[ChartBreakdown | ChartUnits]:
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
    unit_header = _unit_header(words)
    for row, quarter in enumerate(quarters):
        top = titles[0].y1 if row == 0 else row_edges[row - 1]
        bottom = row_edges[row] if row < len(row_edges) else float("inf")
        period = _QUARTER.match(quarter.text.replace(" ", ""))
        for header in titles:
            left, right = bounds[id(header)]
            cell = [word for word in words if left <= word.cx < right and top <= word.cy < bottom and word is not header]
            axis = _TITLES[_squash(header.text)]
            others = set().union(*(names for other, names in _AXIS_NAMES.items() if other != axis))
            shares = {name: share for name, share in _shares(cell).items() if name not in others}
            if any(name not in _AXIS_NAMES[axis] for name in shares):
                continue
            if len(shares) >= 2 and _WHOLE[0] <= sum(shares.values()) <= _WHOLE[1]:
                year = period.group(2) if len(period.group(2)) == 4 else f"20{period.group(2)}"
                charts.append(ChartBreakdown(subject, _TITLES[_squash(header.text)], year, f"Q{period.group(1)}", shares))

    if unit_header is not None:
        # Bars stand lower than the pies beside them, so their rows are told by the lines of technology names under
        # them, top to bottom, one line per quarter.
        # The bars stand under their heading, a little wider than it; the region pie's labels to the left are not bars.
        left, right = unit_header.x0 - 70, unit_header.x1 + 70
        column = [word for word in (detail if detail is not None else words)
                  if left <= word.cx < right and word.cy > unit_header.y1 + 30]
        column = [part for word in column for part in _split_names(word)]
        baselines: list[float] = []
        for word in sorted((word for word in column if _NAMES.get(_squash(word.text)) in _UNIT_NAMES), key=lambda item: item.cy):
            if not baselines or word.cy - baselines[-1] > 40:
                baselines.append(word.cy)
        if len(baselines) == len(quarters):
            for row, (quarter, baseline) in enumerate(zip(quarters, baselines)):
                above = baselines[row - 1] + 12 if row else unit_header.y1 + 30
                cell = [word for word in column if above <= word.cy <= baseline + 12]
                period = _QUARTER.match(quarter.text.replace(" ", ""))
                year = period.group(2) if len(period.group(2)) == 4 else f"20{period.group(2)}"
                if partial:
                    # Every bar this reading gave, however few: the readings are weighed against each other later.
                    units, names = _unit_counts(cell)
                    if names:
                        charts.append(ChartUnits(year, f"Q{period.group(1)}", units,
                                                 next((name for name in names if name not in units), None),
                                                 names=tuple(names)))
                    continue
                units, missing = _units(cell)
                if units:
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


_UNIT_NAME = re.compile(r"arf\s?dry|arfi|euv|krf|[i1]-?line", re.I)


def _split_names(word: Word) -> list[Word]:
    """Technology names that run together under narrow bars ("ArFi ArFdry KrF" read as one word) taken apart again,
    each given its share of the word's width."""
    names = list(_UNIT_NAME.finditer(word.text))
    if len(names) < 2 or _UNIT_NAME.sub("", word.text).strip():
        return [word]
    width = (word.x1 - word.x0) / max(1, len(word.text))
    return [Word(word.x0 + match.start() * width, word.y0, word.x0 + match.end() * width, word.y1, match.group(0))
            for match in names]


def _unit_counts(cell: list[Word]) -> tuple[dict[str, int], list[str]]:
    """Bars read bottom-up: the technologies stand on one line under the bars, and each bar's count is the number
    nearest above its own label. Returns the counts read and every technology on the line."""
    labels = [word for word in cell if _NAMES.get(_squash(word.text)) in _UNIT_NAMES]
    if len(labels) < 3:
        return {}, []
    baseline = max(word.cy for word in labels)
    labels = sorted((word for word in labels if abs(word.cy - baseline) <= 10), key=lambda item: item.cx)
    numbers = [word for word in cell if re.fullmatch(r"\d{1,4}", word.text.strip()) and word.cy < baseline - 10]
    units: dict[str, int] = {}
    taken: set[int] = set()
    for label in labels:
        width = max(30.0, label.x1 - label.x0)
        over = [number for number in numbers if id(number) not in taken and abs(number.cx - label.cx) <= 0.6 * width]
        if not over:
            continue
        count = max(over, key=lambda item: item.cy)  # the lowest number over the label is the bar's own
        taken.add(id(count))
        units[_name(label.text)] = int(count.text.strip())
    if len(taken) != len(numbers):
        return {}, []  # a number that belongs to no bar: this reading is not to be trusted
    return units, [_name(label.text) for label in labels]


def _units(cell: list[Word]) -> tuple[dict[str, int], str | None]:
    """One reading's bars; at most one technology may be left without a count (named as missing)."""
    units, names = _unit_counts(cell)
    missing = [name for name in names if name not in units]
    if not names or len(missing) > 1:
        return {}, None
    return units, (missing[0] if missing else None)


def ocr_words(image: bytes, region: tuple[int, int] | None = None, scale: int = 3) -> list[Word]:
    """The words of a picture with their boxes, by a local OCR model. With `region` (left, top), only the picture
    from there on is read, enlarged and with its contrast stretched, which brings out small grey figures; the boxes
    come back in the whole picture's coordinates."""
    from rapidocr_onnxruntime import RapidOCR  # heavy; loaded only when a chart slide is read

    source: object = image
    left = top = 0
    factor = 1
    if region is not None:
        import io

        import numpy
        from PIL import Image, ImageOps

        left, top = region
        factor = scale
        picture = Image.open(io.BytesIO(image)).convert("RGB")
        part = picture.crop((left, top, picture.width, picture.height))
        part = part.resize((part.width * factor, part.height * factor), Image.LANCZOS)
        source = numpy.array(ImageOps.autocontrast(ImageOps.grayscale(part), cutoff=2).convert("RGB"))
    result, _ = _engine(RapidOCR)(source)
    words = []
    for box, text, _score in result or []:
        xs, ys = [point[0] / factor + left for point in box], [point[1] / factor + top for point in box]
        words.append(Word(min(xs), min(ys), max(xs), max(ys), str(text)))
    return words


_ENGINE = None


def _engine(factory):
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = factory()
    return _ENGINE


# A slide given over to one pie of one quarter, titled in its hidden text ("3Q26 Revenue by Platform", TSMC): the pie
# stands on the left, a chart of growth rates or amounts on the right.
_TITLED_PIE = re.compile(r"\b([1-4])Q(\d{2})\s+Revenue\s+by\s+(Platform)\b", re.I)
_PURE_SHARE = re.compile(r"^(\d{1,3})\s?%$")
# The lettering of a platform pie, as the OCR reads it ("IoT" can come back as "loT" or "101").
_PLATFORM_NAMES = {
    "hpc": "High Performance Computing", "smartphone": "Smartphone", "iot": "Internet of Things", "lot": "Internet of Things",
    "101": "Internet of Things", "1ot": "Internet of Things", "automotive": "Automotive",
    "dce": "Digital Consumer Electronics", "others": "Others", "other": "Others",
}


def titled_pie_slides(raw_html: str) -> list[tuple[str, str, str, str]]:
    """(image, axis, fiscal year, fiscal period) of each slide whose hidden title names one quarter's pie."""
    slides = []
    for source, text in _SLIDE.findall(raw_html or ""):
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()
        match = _TITLED_PIE.search(text)
        if match:
            slides.append((source, match.group(3).lower(), f"20{match.group(2)}", f"Q{match.group(1)}"))
    return slides


def read_titled_pie(words: list[Word], axis: str, fiscal_year: str, fiscal_period: str) -> ChartBreakdown | None:
    """The pie on the left half of a titled slide: each share ("21%") under its label ("Smartphone"). Every platform
    must be found once, and the shares must add up to a whole (rounded to points, 99 to 101)."""
    if axis != "platform" or not words:
        return None
    middle = max(word.x1 for word in words) / 2
    left = [word for word in words if word.cx < middle]
    labels = [word for word in left if _PLATFORM_NAMES.get(_squash(word.text))]
    shares: dict[str, float] = {}
    taken: set[int] = set()
    for share in sorted((word for word in left if _PURE_SHARE.match(word.text.strip())), key=lambda item: item.cy):
        # The label stands right over its share, their centres close.
        above = [label for label in labels if id(label) not in taken and -8 <= share.y0 - label.y1 <= 26
                 and abs(label.cx - share.cx) <= max(45.0, (label.x1 - label.x0) / 2)]
        if not above:
            return None
        label = min(above, key=lambda item: (share.y0 - item.y1) + abs(item.cx - share.cx) / 4)
        taken.add(id(label))
        name = _PLATFORM_NAMES[_squash(label.text)]
        if name in shares:
            return None
        shares[name] = float(_PURE_SHARE.match(share.text.strip()).group(1))
    if set(shares) != set(_PLATFORM_NAMES.values()) or not 99 <= sum(shares.values()) <= 101:
        return None
    return ChartBreakdown("Net revenue", "platform", fiscal_year, fiscal_period, shares)
