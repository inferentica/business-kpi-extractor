"""Earnings documents as numbered tables and text blocks the AI can point into, and the numbers code reads back."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import html

_WHITESPACE = re.compile(r"\s+")
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul", "ol"}
# Cells SEC tables split off their number: currency signs before it, closing parentheses and percent signs after it.
_LEADING_SYMBOLS = {"$", "US$", "NT$", "€", "£", "¥", "C$", "A$", "HK$", "RMB", "(", "($", "$("}
_TRAILING_SYMBOLS = {"%", ")", "%)", ")%", "pts", "bps"}
_TEXT_BLOCK_CHARS = 600
_CONTEXT_CHARS = 300

_NUMBER = re.compile(r"(?P<neg>[-−])?\(?\s*(?:[A-Z]{0,3}\$|€|£|¥)?\s*(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?")
_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_SCALE_AFTER = re.compile(r"^(?:\s*(?P<word>thousand|million|billion|trillion)s?\b|(?P<abbr>bn|B|M|K)\b)", re.I)
_SCALE_ABBREVIATIONS = {"bn": "billion", "b": "billion", "m": "million", "k": "thousand"}
_RELEVANT = re.compile(r"revenue|net sales|\bsales\b|shipment|subscri|users|members|platform|technolog|geograph|region",
                       re.I)
_AMOUNT = re.compile(r"[^\w]*\d[\d,.]*\s*[%)]*")
_TABLE_SCALE = re.compile(r"\b(?:in|amounts in|\(in)\s+(?:[A-Z]{0,3}\$\s*|NT\$\s*|US\$\s*)?(?P<word>thousands|millions|billions)\b", re.I)


_DECLARED_CURRENCIES = (
    (re.compile(r"new taiwan dollars?|\bNT\$|\bNTD\b", re.I), "TWD"),
    (re.compile(r"\beuros?\b|€", re.I), "EUR"),
    (re.compile(r"\brenminbi\b|\bRMB\b", re.I), "CNY"),
    (re.compile(r"\bjapanese yen\b|¥", re.I), "JPY"),
    (re.compile(r"hong kong dollars?|\bHK\$", re.I), "HKD"),
    (re.compile(r"pounds? sterling|£", re.I), "GBP"),
    (re.compile(r"\bU\.?S\.? dollars?\b|\bUS\$|\bUSD\b", re.I), "USD"),
)


def declared_currency(text: str) -> str | None:
    """The one currency an amount declaration names ("Amounts in Thousands of New Taiwan Dollars", "(€, in
    millions)"): only the words around a scale phrase count, never a passing mention of a currency."""
    found = set()
    for match in _TABLE_SCALE.finditer(text or ""):
        before = re.split(r"[.;:]\s", text[max(0, match.start() - 40):match.start()])[-1]  # the same clause only
        window = before + text[match.start():match.end() + 60]
        found |= {code for pattern, code in _DECLARED_CURRENCIES if pattern.search(window)}
    return found.pop() if len(found) == 1 else None


@dataclass
class Table:
    id: str
    context: str
    rows: list[list[str]]

    def cell(self, row: int, col: int) -> str | None:
        if 0 <= row < len(self.rows) and 0 <= col < len(self.rows[row]):
            return self.rows[row][col] or None
        return None

    def declared_scale(self) -> float | None:
        """The multiplier a table's own header or caption declares, e.g. "(in millions)"."""
        header = " ".join(" ".join(row) for row in self.rows[:4])
        match = _TABLE_SCALE.search(f"{self.context[-160:]} {header}")
        return _SCALE_WORDS[match.group("word").lower().rstrip("s")] if match else None

    def render(self) -> str:
        lines = [f"[{self.id}] context: {self.context[-160:]!r}"]
        for index, row in enumerate(self.rows):
            cells = [f"c{col}={text!r}" for col, text in enumerate(row) if text]
            if cells:
                lines.append(f"r{index}: " + " | ".join(cells))
        return "\n".join(lines)


@dataclass
class TextBlock:
    id: str
    text: str

    def render(self) -> str:
        return f"[{self.id}] {self.text}"

    def declared_scale(self) -> float | None:
        """The one amount scale a text block declares, e.g. "(Unaudited, €, in millions)" over figures that reach us as
        text (ASML's statements are slide images with their figures as hidden text)."""
        found = {_SCALE_WORDS[match.group("word").lower().rstrip("s")] for match in _TABLE_SCALE.finditer(self.text)}
        return found.pop() if len(found) == 1 else None


@dataclass
class Document:
    source_url: str
    tables: dict[str, Table] = field(default_factory=dict)
    blocks: dict[str, TextBlock] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def render(self) -> str:
        return "\n\n".join((self.tables.get(key) or self.blocks[key]).render() for key in self.order)

    def fits(self, max_chars: int) -> bool:
        return len(self.render()) <= max_chars

    def default_scale(self) -> float | None:
        """The one amount scale a document declares throughout (TSMC's reports say "in thousands of New Taiwan
        dollars" only in page headings), or None when it declares none or several."""
        found = set()
        for item in [*self.blocks.values(), *self.tables.values()]:
            text = item.text if isinstance(item, TextBlock) else f"{item.context} {' '.join(' '.join(r) for r in item.rows[:3])}"
            for match in _TABLE_SCALE.finditer(text):
                found.add(_SCALE_WORDS[match.group("word").lower().rstrip("s")])
        return found.pop() if len(found) == 1 else None

    def declared_currency(self) -> str | None:
        """The one currency a document's amount declarations name, if any."""
        found = set()
        for item in [*self.blocks.values(), *self.tables.values()]:
            text = item.text if isinstance(item, TextBlock) else f"{item.context} {' '.join(' '.join(r) for r in item.rows[:3])}"
            if code := declared_currency(text):
                found.add(code)
        return found.pop() if len(found) == 1 else None

    def outline(self, max_chars: int = 60_000) -> str:
        """One line per table (caption, header and row labels) and text block (its opening), for choosing sections."""
        lines = []
        for key in self.order:
            if key in self.tables:
                table = self.tables[key]
                header = " | ".join(cell for cell in (table.rows[0] if table.rows else []) if cell)[:120]
                labels = ", ".join(row[0] for row in table.rows[1:16] if row and row[0])[:220]
                lines.append(f"[{key}] {table.context[-90:]!r} header: {header} rows: {labels}")
            else:
                lines.append(f"[{key}] {self.blocks[key].text[:150]}")
        return "\n".join(lines)[:max_chars]

    def render_ids(self, ids: set[str], max_chars: int, opening: int = 6) -> str:
        """The chosen tables and blocks (plus the opening blocks, which name the period) in document order."""
        parts, used = [], 0
        for index, key in enumerate(self.order):
            if index >= opening and key not in ids:
                continue
            text = (self.tables.get(key) or self.blocks[key]).render()
            if used + len(text) > max_chars:
                break
            parts.append(text)
            used += len(text) + 2
        return "\n\n".join(parts)

    def render_for_prompt(self, max_chars: int) -> str:
        """The whole document when it fits; otherwise its opening (which names the period) and every table or passage
        about revenue, sales, shipments or users, in document order and with their original ids. The pipeline prefers
        an AI-chosen selection (render_ids); this keyword filter is the fallback."""
        full = self.render()
        if len(full) <= max_chars:
            return full
        parts, used = [], 0
        for index, key in enumerate(self.order):
            item = self.tables.get(key) or self.blocks[key]
            text = item.render()
            relevant = index < 6 or _RELEVANT.search(
                f"{item.context} {' '.join(' '.join(row) for row in item.rows[:4])} {' '.join(row[0] for row in item.rows if row)}"
                if isinstance(item, Table) else item.text)
            if relevant and used + len(text) <= max_chars:
                parts.append(text)
                used += len(text) + 2
        return "\n\n".join(parts)

    def extend(self, other: "Document") -> None:
        """Appends another exhibit of the same filing; ids stay unique because each exhibit is prefixed."""
        self.tables.update(other.tables)
        self.blocks.update(other.blocks)
        self.order.extend(other.order)


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    percent: bool
    scale_word: str | None


def clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text.replace("\xa0", " ")).strip()


def parse_number(text: str | None) -> ParsedNumber | None:
    """The first number in text, with its sign, percent sign and any scale word ("3.60 billion")."""
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    value = float(match.group("int").replace(",", "") + ("." + match.group("frac") if match.group("frac") else ""))
    before, after = text[: match.start("int")], text[match.end():]
    negative = bool(match.group("neg")) or ("(" in before and ")" in after[:3])
    scale = _SCALE_AFTER.match(after)
    scale_word = None
    if scale:
        scale_word = scale.group("word").lower() if scale.group("word") else _SCALE_ABBREVIATIONS[scale.group("abbr").lower()]
    if scale_word:
        value *= _SCALE_WORDS[scale_word]
    return ParsedNumber(-value if negative else value, "%" in after[:3], scale_word)


def parse_document(raw_html: bytes | str, source_url: str, prefix: str = "") -> Document:
    if isinstance(raw_html, str) and raw_html.lstrip().startswith("<?xml"):
        raw_html = raw_html.encode("utf-8")  # lxml refuses text that declares its own encoding
    root = html.fromstring(raw_html)
    for node in root.xpath("//script|//style|//head"):
        node.drop_tree()
    document = Document(source_url=source_url)
    tables = root.xpath("//table[not(ancestor::table)]")
    for index, element in enumerate(tables):
        rows = _table_rows(element)
        marker = f"\n@@TABLE{index}@@\n"
        if rows and any(re.search(r"\d", cell) for row in rows for cell in row):
            document.tables[f"{prefix}T{index}"] = Table(f"{prefix}T{index}", "", rows)
        else:
            marker = "\n" + " ".join(" ".join(row) for row in rows) + "\n"
        tail = element.tail
        replacement = html.Element("span")
        replacement.text = marker
        replacement.tail = tail
        element.getparent().replace(element, replacement)
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.lower() in _BLOCK_TAGS:
            element.tail = "\n" + (element.tail or "")
    text = root.text_content()
    block_index = 0
    buffer: list[str] = []
    recent = ""

    def flush() -> None:
        nonlocal block_index, buffer
        if buffer:
            key = f"{prefix}P{block_index}"
            document.blocks[key] = TextBlock(key, " ".join(buffer))
            document.order.append(key)
            block_index += 1
            buffer = []

    for line in text.split("\n"):
        stripped = clean(line)
        if not stripped:
            continue
        marker = re.fullmatch(r"@@TABLE(\d+)@@", stripped)
        if marker:
            flush()
            key = f"{prefix}T{marker.group(1)}"
            if key in document.tables:
                document.tables[key].context = recent[-_CONTEXT_CHARS:]
                document.order.append(key)
            continue
        if sum(len(part) for part in buffer) + len(stripped) > _TEXT_BLOCK_CHARS:
            flush()
        buffer.append(stripped)
        recent = f"{recent} {stripped}"[-_CONTEXT_CHARS:]
    flush()
    _tables_from_text(document)
    _tables_from_leaders(document)
    return document


_YEARS_RUN = re.compile(r"\b((?:(?:19|20)\d{2}\s+){1,7}(?:19|20)\d{2})\b")
_DURATION = re.compile(r"(three|six|nine|twelve)\s+months\s+ended|years?\s+ended|quarter\s+ended", re.I)
_NUMBER_CELL = r"\(?-?[\d,]+(?:\.\d+)?\)?\s*%?"


def _row_pattern(numbers: int) -> re.Pattern:
    """A label followed by exactly `numbers` figures."""
    return re.compile(r"\s*([A-Za-z][A-Za-z&,'()/\- ]{2,80}?)\s+((?:" + _NUMBER_CELL + r"\s+){" + str(numbers - 1) + "}"
                      + _NUMBER_CELL + r")(?=\s|$)")


def _tables_from_text(document: Document) -> None:
    """Tables that reach us as text (ASML's statements are slide images with their figures as hidden text): a block
    with a run of years and then rows of a label and as many numbers becomes a table beside the block, headed by its
    periods and durations, so it is read like any other table instead of quoted."""
    for key in list(document.order):
        block = document.blocks.get(key)
        if block is None:
            continue
        years = _YEARS_RUN.search(block.text)
        if not years:
            continue
        columns = years.group(1).split()
        if len(columns) < 2:
            continue
        before, after = block.text[:years.start()], block.text[years.end():]
        pattern = _row_pattern(len(columns))
        rows, position = [], 0
        while True:
            match = pattern.match(after, position)
            if not match:
                break
            rows.append([clean(match.group(1)), *(cell.replace(" ", "") for cell in re.findall(_NUMBER_CELL, match.group(2)))])
            position = match.end()
        if len(rows) < 3:
            continue
        durations = [m.group(0) for m in _DURATION.finditer(before)]
        per = len(columns) // len(durations) if durations and len(columns) % len(durations) == 0 else 0
        header = ["", *([durations[i // per] for i in range(len(columns))] if per else [""] * len(columns))]
        table_key = f"{key}_T"
        document.tables[table_key] = Table(table_key, clean(before)[-_CONTEXT_CHARS:], [header, ["", *columns], *rows])
        document.order.insert(document.order.index(key) + 1, table_key)


def _table_rows(element) -> list[list[str]]:
    """Rows on a column grid, with split-off symbols folded back into their numbers.

    SEC tables put "$" and ")" in cells of their own and give headers a colspan over them, so after folding, a wide
    header is placed on the first numeric column it spans; that keeps "2026" above the 2026 values.
    """
    rows: list[list[tuple[int, int, str]]] = []
    width = 0
    for tr in element.xpath(".//tr"):
        cells: list[tuple[int, int, str]] = []
        column = 0
        for cell in tr.xpath("./td|./th"):
            text = clean(cell.text_content())
            raw_span = str(cell.get("colspan", "1") or "1")
            span = max(int(raw_span), 1) if raw_span.isdigit() else 1
            if text:
                cells.append((column, span, text))
            column += span
        width = max(width, column)
        rows.append(cells)
    folded: list[list[tuple[int, int, str]]] = []
    for cells in rows:
        positions = [""] * width
        for start, _span, text in cells:
            positions[start] = text
        positions = _fold_symbols(positions)
        folded.append([(start, span, positions[start]) for start, span, _text in cells if positions[start]])
    # Numbers are right-aligned: a wide number cell belongs to the last column it spans. TSMC's reports give the "$"
    # row's number a narrower cell than the rows below it, but every amount in a column ends at the same grid line.
    right_edges: dict[int, int] = {}
    for cells in folded:
        for start, span, text in cells:
            if span > 1 and _AMOUNT.fullmatch(text):
                right_edges[start + span - 1] = right_edges.get(start + span - 1, 0) + 1
    numeric_columns = {start for cells in folded for start, span, text in cells if span == 1 and re.search(r"\d", text)}
    numeric_columns |= {column for column, rows in right_edges.items() if rows >= 2}
    grid = []
    for cells in folded:
        row = [""] * width
        for start, span, text in cells:
            anchors = [column for column in range(start, start + span) if column in numeric_columns] if span > 1 else []
            column = anchors[0] if anchors else start
            row[column] = f"{row[column]} {text}".strip() if row[column] else text
        grid.append(row)
    used = [column for column in range(width) if any(row[column] for row in grid)]
    return [[row[column] for column in used] for row in grid if any(row[column] for column in used)]


def _fold_symbols(cells: list[str]) -> list[str]:
    cells = list(cells)
    for index, text in enumerate(cells):
        if text in _LEADING_SYMBOLS:
            target = next((j for j in range(index + 1, len(cells)) if cells[j]), None)
            if target is not None and re.search(r"\d", cells[target]):
                cells[target] = f"{text}{cells[target]}"
                cells[index] = ""
    for index, text in enumerate(cells):
        if text in _TRAILING_SYMBOLS:
            target = next((j for j in range(index - 1, -1, -1) if cells[j]), None)
            if target is not None and re.search(r"\d", cells[target]):
                cells[target] = f"{cells[target]}{text}"
                cells[index] = ""
    return cells


_LEADER = re.compile(r"\s*\.{3,}\s*")
_LEADER_CELL = re.compile(r"\s*(?:[>~<]\s*)?(?:\$\s*)?(?:\(?-?\d[\d,]*(?:\.\d+)?\)?(?:\s*%)?|—|–)(?=\s|$)")
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE = re.compile(_MONTH + r"\s+\d{1,2},\s+(?:19|20)\d{2}")
_DATES_RUN = re.compile(r"(?:" + _MONTH + r"\s+\d{1,2},\s+(?:19|20)\d{2}\s*){2,8}")


def _tables_from_leaders(document: Document) -> None:
    """Statements that reach us as text from a PDF (UnitedHealth's releases): each row a label, a dot leader and its
    figures, in one block per page. The rows become tables beside the block, a new table at each header (a run of dates,
    or of years under their durations), with a period line such as "Three Months Ended June 30, 2025" kept as a row of
    its own; the block keeps only its words, so the figures are not shown twice."""
    for key in list(document.order):
        block = document.blocks.get(key)
        if block is None or f"{key}_T" in document.tables or len(_LEADER.findall(block.text)) < 3:
            continue
        pieces = _LEADER.split(block.text)
        tables: list[tuple[str, list[list[str]]]] = []
        gap, kept = pieces[0], []
        for piece in pieces[1:]:
            header, section, label = _split_gap(gap, first=not tables)
            if header is not None:
                tables.append(header)
            elif not tables:
                break
            rows = tables[-1][1]
            if section:
                rows.append([section])
            cells, position = [], 0
            while match := _LEADER_CELL.match(piece, position):
                cells.append(re.sub(r"[\s>~<$]", "", match.group(0)))
                position = match.end()
            if cells:
                rows.append([clean(label), *cells])
            gap = piece[position:]
        kept.append(gap)
        built = [(context, rows) for context, rows in tables
                 if sum(1 for row in rows if len(row) > 1 and any(_NUMBER.search(cell) for cell in row[1:])) >= 3]
        if not built:
            continue
        place = document.order.index(key) + 1
        for index, (context, rows) in enumerate(built):
            table_key = f"{key}_L{index}"
            document.tables[table_key] = Table(table_key, context, rows)
            document.order.insert(place + index, table_key)
        block.text = clean(f"{built[0][0]} {' '.join(kept)}")


def _split_gap(gap: str, first: bool) -> tuple[tuple[str, list[list[str]]] | None, str | None, str]:
    """The text between one row's figures and the next row's leader: a new table's header (when it holds a run of
    dates or years, or opens the block), a period line, and the next row's label."""
    text = clean(gap)
    run = _DATES_RUN.search(text) or _YEARS_RUN.search(text)
    if run:
        before = text[:run.start()]
        columns = _DATE.findall(run.group(0)) or run.group(1).split()
        durations = [m.group(0) for m in _DURATION.finditer(before)]
        per = len(columns) // len(durations) if durations and len(columns) % len(durations) == 0 else 0
        header = [["", *([durations[i // per] for i in range(len(columns))])]] if per else []
        return (before[-_CONTEXT_CHARS:], [*header, ["", *columns]]), None, text[run.end():]
    date = None
    for date in _DATE.finditer(text):
        pass
    if date is None:
        return ((text[-_CONTEXT_CHARS:], []), None, "") if first else (None, None, text)
    durations = [m for m in _DURATION.finditer(text, 0, date.start()) if date.start() - m.start() <= 40]
    start = durations[-1].start() if durations else date.start()
    section, label = text[start:date.end()], text[date.end():]
    if first:
        return (text[:start][-_CONTEXT_CHARS:], []), section, label
    return None, section, label
