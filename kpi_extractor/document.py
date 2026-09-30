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
_TABLE_SCALE = re.compile(r"\b(?:in|amounts in|\(in)\s+(?:[A-Z]{0,3}\$\s*|NT\$\s*|US\$\s*)?(?P<word>thousands|millions|billions)\b", re.I)


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
    return document


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
    numeric_columns = {start for cells in folded for start, span, text in cells if span == 1 and re.search(r"\d", text)}
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
