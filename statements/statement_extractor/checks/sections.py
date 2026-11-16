"""Each cash flow section's total against its own lines, signs as printed: "Net cash used in investing activities" is
the sum of the lines its heading opens. The roll-forward alone (opening cash plus the change equals closing cash) holds
whatever a line inside a section reads (review T1b): a lost bracket, a note number read as the figure (CSN's 2017
dividends: note 20 where the 20-F prints no amount), or a filing's own typo (Oshkosh's FY1994 10-K prints 1993 net
income as $11,063 in its cash flows and 1,063 in its income statement; Sabesp's 2015 20-F prints "(498.669)" among
thousands). A statement that does not add up is then served from another filing's copy that does.

Only a section its own heading opens is tested ("Cash flows from investing activities", then its lines, then its
total). Releases that print a section's total above its lines, or no headings at all, are left untested, as is the IFRS
taxonomy's template that BMV's forms print, whose amounts carry their elements' signs rather than the arithmetic."""
from __future__ import annotations

import re

from ..domain.model import FAIL, NONE, PASS, Check
from ..domain.printed_labels import CLOSING, OPENING, SECTION_KINDS
from .subtotals import subtotals
from .tolerance import close

# A section's total: "Net cash provided by (used in) investing activities", "Cash generated from operations", EOG's
# "Net operating cash inflows"; never a line within one ("Cash inflow from other financing activities", UBS's "Net loss
# / (gain) from investing activities", KKR's "Net realized (gains) losses on insurance operations").
_TOTAL_END = re.compile(r"(operat|invest|financ)\w*\s+activities(\s+(from|of|for)\s+(continuing|discontinued)\s+operations)?\W*$|"
                        r"^(net\s+)?cash\b.*\boperations\W*$|cash\s+(in|out)?flows?\W*$", re.I)
_NOT_TOTAL = re.compile(r"\bother\b|\bgains?\b|\blosses\b|\(gain|realized|contributed|received\s+from", re.I)
# The heading that opens a section's lines.
_HEAD = re.compile(r"\b(operat\w*|invest\w*|financ\w*)\s+activities\b|^(operating|investing|financing)\b", re.I)
# Lines printed as plain figures to be taken away: Woori's "Deductions of revenues not involving cash inflows" and
# "Cash out-flows from investing activities", each with its own unlabelled total.
_DEDUCTIONS = re.compile(r"^(deductions?|less|cash\s+out-?\s?flows?)\b", re.I)
# The IFRS taxonomy's own headings, as BMV's forms print them (Televisa, Volaris since 2015).
_TEMPLATE = re.compile(r"^(cash\s+flows\s+from\s+\(used\s+in\)\s+(operating|investing|financing)\s+activities|"
                       r"adjustments\s+to\s+reconcile\s+profit\s+\(loss\))\s*(\[abstract\])?$", re.I)
# Subtotals the statement names as such, read through before any other.
_NAMED = re.compile(r"^$|^(sub-?\s?)?total\b|^net\b|^cash\b|\b(changes?|adjusted|adjustments)\b", re.I)
_REACH = 40


def section_total(label: str) -> bool:
    return bool(SECTION_KINDS.search(label) and _TOTAL_END.search(label) and not _NOT_TOTAL.search(label)
                and not label.rstrip().endswith(":"))


def section_check(printed) -> Check:
    """printed: (label, level, value) in printed units, headings with no value, as the printed reader holds them."""
    if any(value is None and ("[abstract]" in label.lower() or _TEMPLATE.match(label)) for label, _level, value in printed):
        return Check("sections", NONE, "the IFRS taxonomy's template")
    printed = _without_parts(_deducted(_headings_with_totals(printed)))
    starts = subtotals(printed)
    for end, start in _closing_runs(printed, starts).items():
        starts.setdefault(end, start)
    stop = next((index for index, (label, _level, value) in enumerate(printed) if value is not None and CLOSING.search(label)),
                len(printed))
    totals = [(index, SECTION_KINDS.search(label).group(2).lower()) for index, (label, _level, value) in enumerate(printed[:stop])
              if value is not None and section_total(label)]
    failures, tested, begin, kind, earlier, after = [], 0, 0, None, [], 0
    for index, section in totals:
        if index < after:
            continue  # among the lines of a total printed over them
        if section != kind:
            begin, kind, earlier = after, section, []
        lines = [(position, label, value) for position, (label, _level, value) in enumerate(printed[begin:index], begin)
                 if value is not None and not OPENING.search(label)]
        inner = [(end, start) for end, start in starts.items() if begin <= start and end < index]
        same = {position for end, start in earlier for position in range(start, end)}  # a continuing total before it
        covered = {position for end, start in inner for position in range(start, end)} | same
        earlier.append((index, starts.get(index, begin)))
        after = index + 1
        own = [line for line in lines if line[0] not in covered]
        if not _headed(printed, begin, index) and not (len(earlier) > 1 and _headed(printed, earlier[0][1], earlier[0][0])):
            continue
        if re.search(r"discontinu", printed[index][0], re.I):
            continue  # one line, its detail in a note
        if len(own) < 2:
            # A total printed over its own lines: its lines run to the next section total.
            nxt = next((later for later, _kind in totals if later > index and printed[later][1] <= printed[index][1]
                        and (later > index + 1 or printed[later][1] < printed[index][1])), stop)
            below, last = _sums_below(printed, index, nxt) if not own else (None, index)
            if below is not None:
                tested, after = tested + 1, last + 1
                if not below:
                    failures.append(f"{printed[index][0][:50]} {printed[index][2]:,.1f} vs its lines below")
            continue
        tested += 1
        if index in starts:
            continue
        value = printed[index][2]
        # Each reading of the subtotals above it, as the income check reads them: all, the named ones, none, each alone;
        # or a line over its own lines (Suzano's adjustments to net income).
        named = {position for end, start in inner if _NAMED.search(printed[end][0]) for position in range(start, end)}
        skips = [covered, named | same, same] + [set(range(start, end)) | same for end, start in inner]
        sums = [[(label, part) for position, label, part in lines if position not in skip] for skip in skips]
        sums.append([(label, part) for label, _level, part in _parts_first([(label, 0, part) for _p, label, part in own])])
        if any(close(abs(sum(_signed(label, part) for label, part in parts)), abs(value), len(parts) + 1) for parts in sums if parts):
            continue
        failures.append(f"{printed[index][0][:50]} {value:,.1f} vs {sum(_signed(label, part) for _p, label, part in own):,.1f}")
    if failures:
        return Check("sections", FAIL, "; ".join(failures[:3]))
    return Check("sections", PASS if tested else NONE, f"{tested} sections")


def _signed(label: str, value: float) -> float:
    return -abs(value) if re.match(r"^less\b", label, re.I) else value


def _headed(printed, begin: int, index: int) -> bool:
    """The section's own heading opens its lines: a row with no figure naming its kind, before any of its lines."""
    for label, _level, value in printed[begin:index]:
        if value is not None:
            return False
        if _HEAD.search(label):
            return True
    return False


def _headings_with_totals(printed) -> list:
    """A heading carrying the total of the lines under it is read as a heading ("Adjustment to net income: (498,443)",
    Woori's parent 6-Ks); one whose lines do not sum to it is a line whose label was lost (Alcon's 2006 "Cash provided
    by (used in) financing activities: (1,864)")."""
    kept = []
    for index, (label, level, value) in enumerate(printed):
        if value is not None and label.rstrip().endswith(":"):
            run, count = 0.0, 0
            for later_label, _level, later in printed[index + 1:]:
                if later is None or section_total(later_label) or later_label.rstrip().endswith(":"):
                    break
                run, count = run + later, count + 1
            if count >= 2 and close(run, value, count + 1):
                value = None
        kept.append((label, level, value))
    return kept


def _deducted(printed) -> list:
    """Lines under a heading that takes them away are negated, their total too."""
    kept, under = [], False
    for label, level, value in printed:
        if value is None:
            under = bool(_DEDUCTIONS.match(label))
        elif under and value >= 0:
            value = -value
        if value is not None and label and section_total(label):
            under = False
        kept.append((label, level, value))
    return kept


def _without_parts(lines) -> list:
    """Lines with their parts printed below them a level deeper set aside (Televisa's earlier 6-Ks, CSN's releases)."""
    kept, skip = [], 0
    for index, (label, level, value) in enumerate(lines):
        if skip:
            skip -= 1
            continue
        kept.append((label, level, value))
        if value is None:
            continue
        running = 0.0
        for count, (_label, part_level, part) in enumerate(lines[index + 1:index + 1 + _REACH], start=1):
            if part is None or part_level <= level:
                break
            running += part
            if count >= 2 and close(running, value, count + 1) and (value != 0 or count == 2):
                skip = count
                break
    return kept


def _parts_first(lines) -> list:
    """Lines with each one's parts printed straight below it at any level set aside, three parts or more."""
    kept, skip = [], 0
    for index, (label, level, value) in enumerate(lines):
        if skip:
            skip -= 1
            continue
        kept.append((label, level, value))
        if not value:
            continue
        running = 0.0
        for count, (_label, _level, part) in enumerate(lines[index + 1:index + 1 + _REACH], start=1):
            if part is None:
                break
            running += part
            if count >= 3 and close(running, value, count + 1):
                skip = count
                break
    return kept


def _closing_runs(printed, starts: dict[int, int]) -> dict[int, int]:
    """A line closing the lines above it in either sign, across headings: Woori's outflows printed as plain figures, then
    their unlabelled total in brackets, or across a page's stray heading; Sabesp's "Adjusted net income" over its
    adjustments. Unlabelled and "Total" lines close two lines or more, others three."""
    found = {}
    for index, (label, _level, value) in enumerate(printed):
        if value is None or index in starts:
            continue
        least = 2 if not label or re.match(r"^(sub-?\s?)?total\b", label, re.I) else 3
        run, count, position = 0.0, 0, index - 1
        while position >= 0 and count < 60:
            earlier_label, _level, earlier = printed[position]
            if earlier is not None:
                if section_total(earlier_label):
                    break
                run, count = run + earlier, count + 1
                if count >= least and close(abs(run), abs(value), count + 1):
                    found[index] = position
                    break
            position -= 1
    return found


def _sums_below(printed, index: int, end: int) -> tuple[bool | None, int]:
    """A total printed over its lines, deeper than it or under its own heading form ("Cash flows from (used in)
    investing activities", then its lines): whether they sum to it, and where they end."""
    heading = re.match(r"^cash\s+flows?\s+(from|provided|used|generated)\b", printed[index][0], re.I)
    below, last = [], index
    for position, (label, level, value) in enumerate(printed[index + 1:end], index + 1):
        if level <= printed[index][1] and not heading:
            break
        if value is not None:
            below.append((label, level, value))
        last = position
    if len(below) < 2:
        return None, index
    for lines in (below, _parts_first(below)):
        if close(abs(sum(value for _label, _level, value in lines)), abs(printed[index][2]), len(lines) + 1):
            return True, last
    return False, last
