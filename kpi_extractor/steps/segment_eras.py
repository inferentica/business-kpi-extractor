"""A listed line named after one of the company's XBRL segments holds no period before that segment existed or after it
ended: McDonald's "International Operated Markets" began in 2019, and its 2016 release's "Total International Lead
Markets" (another set of countries) was read and confirmed as that line. Only where the company's XBRL tags segments for
the period and the named segment is outside the run of periods it is tagged in: a quarter whose XBRL leaves one segment
out says nothing. No AI involved."""
from __future__ import annotations

from ..quality.reasons import OUTSIDE_SEGMENT_ERA
from ..xbrl import member_key

SEGMENTS = "segments"
_MIN_PREFIX = 8  # "International Developmental Licensed Markets" is tagged "... Markets and Corporate"


def _order(year: str, period: str) -> tuple[int, int]:
    return int(year), 5 if period == "FY" else int(period[1])


def lines_outside_segment_eras(values: dict) -> list[dict]:
    tagged: dict[tuple[str, str], set[str]] = {}
    for v in values.values():
        if v["group_key"] == SEGMENTS and v["method"] in ("xbrl", "derived") and v["validation_status"] != "rejected":
            tagged.setdefault((v["fiscal_year"], v["fiscal_period"]), set()).add(v["kpi_key"])
    if not tagged:
        return []
    spans: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {}
    for (year, period), members in tagged.items():
        for member in members:
            first, last = spans.get(member, (_order(year, period), _order(year, period)))
            spans[member] = (min(first, _order(year, period)), max(last, _order(year, period)))
    def segment_of(label: str) -> str | None:
        name = member_key(label)
        named = [member for member in spans if member == name or (len(name) >= _MIN_PREFIX and member.startswith(name))]
        return named[0] if len(named) == 1 else None

    # Only a group of segments: two or more of its lines name one (JPMorgan's client coverage groups list "Commercial
    # Banking" beside Global Corporate and Investment Banking, a client group since its segment of that name ended).
    lines: dict[str, set[str]] = {}
    for v in values.values():
        if v["method"] == "ai" and segment_of(v["kpi_label"]):
            lines.setdefault(v["group_key"], set()).add(v["kpi_key"])
    held = []
    for v in values.values():
        if v["method"] != "ai" or v["validation_status"] != "verified" or (v["fiscal_year"], v["fiscal_period"]) not in tagged \
                or len(lines.get(v["group_key"], ())) < 2 or not (segment := segment_of(v["kpi_label"])):
            continue
        first, last = spans[segment]
        if not first <= _order(v["fiscal_year"], v["fiscal_period"]) <= last:
            held.append({**v, "validation_status": "needs_review", "notes": [*(v.get("notes") or []), OUTSIDE_SEGMENT_ERA]})
    return held
