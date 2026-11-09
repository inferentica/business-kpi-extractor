"""Where a plain-text document's tables are: its <TABLE> blocks, and statements printed with no tags at all."""
from __future__ import annotations

import re

from ..printed.tables import COLUMN_TITLE
from .lines import YEAR, chunks, split

BLOCK = re.compile(r"<TABLE>(.*?)</TABLE>", re.I | re.S)
TAG = re.compile(r"</?(S|C|CAPTION|PAGE|FN|F\d+|TABLE)>", re.I)

_UNTAGGED_TITLE = re.compile(r"^[ \t]*(condensed\s+)?(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?"
                             r"(income|operations|earnings|cash\s+flows?|financial\s+(position|condition)))\b", re.I | re.M)
_UNTAGGED_END = re.compile(r"^\s*(see\s+(accompanying\s+)?notes|the\s+accompanying\s+notes)|<PAGE>|<TABLE>", re.I)


def tagged(text: str):
    """(position, before, title lines, header lines, body lines) for each <TABLE> block, and for each statement a block
    prints after another one: ASML's 2003 releases run their cash flows on in the balance sheet's block, and a block of
    ratios on after the income statement."""
    previous_end = 0
    for block in BLOCK.finditer(text):
        before = text[max(previous_end, block.start() - 1500):block.start()]
        previous_end = block.end()
        raw = block.group(1).splitlines()
        caption = next((index for index, line in enumerate(raw) if re.search(r"<CAPTION>", line, re.I)), None)
        marker = next((index for index, line in enumerate(raw) if re.search(r"<[SC]>", line, re.I)), None)
        title_lines = raw[:caption] if caption is not None else []
        header_start = caption + 1 if caption is not None else 0
        header_end = marker if marker is not None and marker >= header_start else header_start
        header = [TAG.sub(" ", line) for line in raw[header_start:header_end]]
        body = [TAG.sub(" ", line) for line in raw[header_end:]]
        # A statement's first lines printed above the <S> marker (ASML's 2003 net sales and cost of sales).
        first = next((index for index, line in enumerate(header) if _figure_line(line, 2)), None)
        if first is not None:
            header, body = header[:first], header[first:] + body
        cuts = _sections(body)
        yield block.start(), before, title_lines, header, body[:cuts[0]] if cuts else body
        for number, (start, end) in enumerate(zip(cuts, [*cuts[1:], len(body)]), start=1):
            lines = body[start:end]
            heading = next(index for index, line in enumerate(lines) if _column_header(line, lines[index + 1:index + 4]))
            opening = next((index for index, line in enumerate(lines) if _figure_line(line)), len(lines))
            yield block.start() + number, "", lines[:heading], lines[heading:opening], lines[opening:]


def _figure_line(line: str, least: int = 1) -> bool:
    parsed = split(line)
    return bool(parsed and parsed.label and len(parsed.figures) >= least
                and not all(YEAR.match(chunk.text) for chunk in parsed.figures))


def _column_header(line: str, after: list[str]) -> bool:
    """A row of years ("2002   2003"), or column titles over one ("For the three months ended   March 31,")."""
    words = chunks(line)
    if len(words) >= 2 and all(YEAR.match(word.text) for word in words):
        return True
    following = next((later for later in after if later.strip()), "")
    return bool(COLUMN_TITLE.search(line)) and not _figure_line(line) and \
        len(chunks(following)) >= 2 and all(YEAR.match(word.text) for word in chunks(following))


def _sections(body: list[str]) -> list[int]:
    """Where a block's later statements start: at the headings after the last figures above a new header."""
    cuts, seen, last_figure = [], False, -1
    for index, line in enumerate(body):
        if seen and _column_header(line, body[index + 1:index + 4]):
            cuts.append(last_figure + 1)
            seen = False
        if _figure_line(line):
            seen, last_figure = True, index
    return cuts


def untagged(text: str, spans: list[tuple[int, int]]):
    """Statements printed with no <TABLE> tags (Coca-Cola's 10-Qs of 2002-2004): from a statement's title line to the
    page's end, the lines before the first figures being its title and header."""
    previous_end = 0
    for match in _UNTAGGED_TITLE.finditer(text):
        if any(start <= match.start() < end for start, end in spans) or match.start() < previous_end:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        lines = text[line_start:line_start + 20_000].splitlines()
        block = []
        has_figures = False
        for number_of_line, line in enumerate(lines):
            if number_of_line and (_UNTAGGED_END.search(line) or _UNTAGGED_TITLE.match(line) and has_figures):
                break  # the page's end, or the next statement once this one has figures
            block.append(line)
            parsed = split(line)
            has_figures = has_figures or bool(parsed and parsed.figures and parsed.label)
            if len(block) > 160:
                break
        first = next((index for index, line in enumerate(block)
                      if (parsed := split(line)) and parsed.figures and parsed.label
                      and not all(YEAR.match(chunk.text) for chunk in parsed.figures)), None)
        if first is None:
            continue
        top = block[:first]
        headed = [index for index, line in enumerate(top) if COLUMN_TITLE.search(line) or
                  any(YEAR.match(chunk.text) for chunk in chunks(line))]
        split_at = headed[0] if headed else first
        # The text before is this statement's own, never the statement before it: Apple's 1996 balance sheet would
        # otherwise take the "(in thousands)" of the share line ending its income statement as a second scale.
        before = text[max(previous_end, line_start - 1500):line_start]
        previous_end = line_start + sum(len(line) + 1 for line in block)
        yield line_start, before, top[:split_at], top[split_at:], block[first:]


