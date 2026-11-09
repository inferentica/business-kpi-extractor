"""HTML tables as printed statements: each row's cells laid out on the table's physical columns (a cell spanning three
columns covers three), so a figure lines up with the header above it. A label's indentation is how far right its cell
starts. Filings before XBRL (about 2001-2008) print their statements this way.
"""
from __future__ import annotations

import re

import lxml.html

from ...domain.basis import currencies_named, is_currency_word
from ..printed.tables import COLUMN_TITLE, is_subheader, NOTE_MARK, Cell, Row, Table, clean, is_header_figures, number


def _text(element) -> str:
    try:
        return clean(element.text_content())
    except ValueError:  # a comment or processing instruction
        return ""


def _cells(tr) -> list[Cell]:
    cells, position = [], 0
    for td in tr:
        if not isinstance(td.tag, str) or td.tag not in ("td", "th"):
            continue
        try:
            span = max(1, int(td.get("colspan") or 1))
        except ValueError:
            span = 1
        cells.append(Cell(position, span, _text(td)))
        position += span
    return cells


def _row(cells: list[Cell]) -> Row | None:
    """A row's label (the text before its first figure) and figures. A closing bracket in a cell of its own makes the
    figure before it negative; "$" and "%" cells are not figures."""
    label_parts, figures, level = [], {}, None
    last_figure = None
    for cell in cells:
        text = cell.text
        if not text:
            continue
        if text in (")", ")%") and last_figure is not None:
            figures[last_figure] = -abs(figures[last_figure])
            continue
        if text in ("$", "%", "€", "NT$", "US$") or text.endswith("%") and number(text.rstrip("%")) is not None:
            continue
        value = number(text.rstrip(")")) if text.endswith(")") and not text.startswith("(") else number(text)
        if value is not None and not label_parts and re.fullmatch(r"\d{1,2}", text):
            continue  # a note reference, not a figure
        if value is not None and not label_parts and not figures and cell.start > 0:
            figures[cell.start] = value  # an unlabelled subtotal (PepsiCo's net property, plant and equipment)
            last_figure = cell.start
            continue
        if value is not None and (figures or label_parts):
            if text.endswith(")") and not text.startswith("("):
                value = -abs(value)
            figures[cell.start] = value
            last_figure = cell.start
            continue
        if text.startswith("(") and not text.endswith(")") and number(text + ")") is not None and (figures or label_parts):
            figures[cell.start] = -abs(number(text + ")"))  # "(1,234" with its bracket in the next cell
            last_figure = cell.start
            continue
        if figures:
            continue  # text after the figures (a footnote mark)
        if re.fullmatch(r"\d{1,2}(\s*,\s*\d{1,2})*", text):
            continue  # a note reference before or after the label (ASML's and TSMC's "Notes" columns)
        if level is None:
            level = cell.start
        label_parts.append(text)
    label = NOTE_MARK.sub("", " ".join(label_parts)).strip()
    label = re.sub(r"\s+\d{1,2}(\s*,\s*\d{1,2})+$|\s+\d{1,2},$", "", label)  # "NET SALES 2, 26" in one cell
    if not label and not figures:
        return None
    return Row(label=label, level=level if level is not None else 0, figures=figures, cells=cells)


def _currency_row(cells: list[Cell]) -> bool:
    """A row naming each column's currency, after a caption at most: TSMC's "NT$ | NT$ | US$", ASML's "Notes | (in
    thousands, except per share data) | EUR | EUR | EUR | US$"."""
    named = [cell for cell in cells if cell.text]
    money = [cell for cell in named if is_currency_word(cell.text)]
    if not money or not all(cell.start < money[0].start for cell in named if cell not in money):
        return False
    # One currency alone names a translation column over its figures (TSMC's 2002 cash flows: "U.S.$ (Note 4)").
    return len(money) > 1 or money[0].start > 0 and bool(currencies_named(money[0].text))


_OPEN_ENDED = re.compile(r"\b(at|of|for|and|in|to|from|on|by|with|the|net\s+of|before|after)\s*$", re.I)


def _rows(lines: list[list[Cell]]) -> tuple[list[Row], list[list[Cell]], bool]:
    """A table's rows, its header rows (those above the first figures) and whether it has figures."""
    rows, header, seen_figures = [], [], False
    for cells in lines:
        if not seen_figures and (_currency_row(cells) or is_subheader(cells)):
            header.append([cell for cell in cells if cell.text])
            continue
        row = _row(cells)
        if row is None:
            continue
        if row.figures and not is_header_figures(row):
            seen_figures = True
            previous = rows[-1] if rows else None
            if previous is not None and not previous.figures and previous.label and row.label[:1].islower() \
                    and not previous.label.rstrip().endswith(":") and row.level >= previous.level:
                # A label wrapped onto the row below, which holds the figures (Scotiabank's 2003 40-F: "Subordinated"
                # / "debentures   26   26   45").
                rows[-1] = Row(label=f"{previous.label} {row.label}", level=previous.level, figures=row.figures,
                               cells=row.cells)
                continue
            rows.append(row)
        elif not seen_figures:
            texts = " ".join(cell.text for cell in cells)
            if COLUMN_TITLE.search(texts):
                header.append([cell for cell in cells if cell.text])
            elif row.label:
                rows.append(Row(label=row.label, level=row.level, cells=cells))
        elif rows and rows[-1].figures and row.label[:1].islower() and _OPEN_ENDED.search(rows[-1].label):
            # A label run on below its figures (Scotiabank's May 2003 6-K: "Cash and cash equivalents at  255" /
            # "end of period").
            rows[-1] = Row(label=f"{rows[-1].label} {row.label}", level=rows[-1].level, figures=rows[-1].figures,
                           cells=rows[-1].cells)
        else:
            rows.append(row)
    return rows, header, seen_figures


def _side_by_side(lines: list[list[Cell]]) -> int | None:
    """Where a second statement starts beside the first: a column where most figure rows print a label after figures
    and more figures after it."""
    starts: dict[int, int] = {}
    figured = 0
    for cells in lines:
        texts = [cell for cell in cells if cell.text]
        kinds = [(cell.start, number(cell.text) is not None or cell.text in ("$", ")", "-", "—")) for cell in texts]
        if not any(is_figure for _start, is_figure in kinds):
            continue
        figured += 1
        for position, (start, is_figure) in enumerate(kinds):
            # Two figure cells on each side at least: a note number before a label is no statement beside it (ASML).
            if not is_figure and start > 0 and sum(f for _s, f in kinds[:position]) >= 2 and \
                    sum(f for _s, f in kinds[position + 1:]) >= 2:
                starts[start] = starts.get(start, 0) + 1
                break
    if not starts or figured < 6:
        return None
    middle, count = max(starts.items(), key=lambda item: item[1])
    return middle if count >= max(5, figured * 0.3) else None


def parse_html(html: str | bytes) -> list[Table]:
    """Every table of a document with rows, its header rows and the text before it."""
    raw = html.encode("utf-8") if isinstance(html, str) else html
    # A block break separates words: PepsiCo's title cell holds "Condensed Consolidated Balance Sheet" and "PepsiCo,
    # Inc." in two divs, which would otherwise read as "Balance SheetPepsiCo".
    raw = re.sub(rb"(?i)(</(?:div|p|li|h[1-6])>|<br\s*/?>)", rb"\1 ", raw)
    # A footnote mark in superscript is not part of the figure or year beside it (Intel's "2008<sup>1</sup>"); a
    # superscript before "/" is a fraction's numerator (PepsiCo's "1<sup>2</sup>/3¢" par value) and stays.
    raw = re.sub(rb"(?is)<sup[^>]*>\s*(?:<[^>]+>\s*)*(?:[\w*]|\xe2\x80[\xa0\xa1]){1,2}\s*(?:</[^>]+>\s*)*</sup>(?!\s*(?:<[^>]+>\s*)*[/\xe2\x81\x84])",
                 b"", raw)
    tree = lxml.html.fromstring(raw)
    found = []
    previous_text: list[str] = []
    for index, element in enumerate(tree.iter()):
        if not isinstance(element.tag, str):
            continue
        if element.tag == "table":
            if element.getparent() is not None and any(a.tag == "table" for a in element.iterancestors()):
                continue
            lines = [_cells(tr) for tr in element.iter("tr")]
            rows, header, seen_figures = _rows(lines)
            middle = _side_by_side(lines)
            if middle is not None:
                # Assets on the left and liabilities on the right of each row (TSMC's consolidated half-year balance
                # sheets): the right half is read after the left, on the left's columns.
                rows, header, seen_figures = _rows([[cell for cell in cells if cell.start < middle] for cells in lines])
                right, _header, _seen = _rows([[Cell(cell.start - middle, cell.span, cell.text) for cell in cells
                                                if cell.start >= middle] for cells in lines])
                rows += right[next((index for index, row in enumerate(right) if row.figures), len(right)):]
            context = " ".join(previous_text)[-600:]
            found.append(Table(rows=rows, header=header, context=context, index=len(found)))
            if seen_figures:
                previous_text = []
            else:
                # A table without figures is the page's layout: its words lead into the next table (Morgan Stanley
                # titles each statement in a one-row table beside its logo).
                words = [clean(" ".join(cell.text for cell in _cells(tr))) for tr in element.iter("tr")]
                previous_text = (previous_text + [word for word in words if word])[-40:]
        elif element.tag in ("p", "div", "font", "b", "i", "em", "strong", "u", "small", "a", "center", "h1", "h2", "h3",
                             "h4", "span", "td") and \
                not any(a.tag == "table" for a in element.iterancestors()):
            if element.text and element.text.strip():
                previous_text.append(clean(element.text))
            for child in element:
                if child.tail and child.tail.strip():
                    previous_text.append(clean(child.tail))
            previous_text = previous_text[-40:]
    return found
