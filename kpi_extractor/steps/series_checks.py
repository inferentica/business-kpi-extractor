"""Checks across periods: proving or clearing flagged figures, scale runs, and working out derived periods."""
from __future__ import annotations

from collections import Counter

import math
import re
from datetime import date, timedelta

from ..ai import Spec
from ..domain.derive import derive_periods, same_derivation
from ..documents.document import Document, parse_number
from ..quality.checks import count_jump, parts_add_up, reported_matches
from ..readers.extract import _PER_UNIT_KPI, ReadValue, column_header
from ..sources.base import FilingRef
from .shared import (AI_GROUP_PREFIX, _UNANCHORED, _days, _is_jump, _rounding, _same_chain, _significant,
                     _year_ago_cells, list_parents, value_key)

MAX_YEAR_RATIO = 3.0
# Evidence that settles a flagged value without anyone looking at it; it outranks the check of a read total row.
_EQUALS_XBRL = "equals a figure reported in XBRL"
_ADDS_TO_XBRL = "parts add up to a figure reported in XBRL"
_CONFIRMED_LATER = "confirmed by the next year's release"
_RESTATED_LATER = "restated in the next year's release"
_WITHIN_XBRL = "parts make up nearly all of a figure reported in XBRL"
TIES_OUT = "its row gives last year's accepted figure for the quarter: it stands on its own"
_PROOFS = (_EQUALS_XBRL, _ADDS_TO_XBRL, _CONFIRMED_LATER, _RESTATED_LATER, _WITHIN_XBRL, TIES_OUT)
# A split of part of a reported line (Adobe's two customer groups: 99.5% of its subscription revenue, the rest its
# advertising business's) covers at least this share of it.
_MIN_COVERAGE = 0.99
_EXACT_NOTE = re.compile(r"^the [\w-]+'s exact (?:figure|year less its three quarters);")
# Restatements move a figure a little (a reclassification); a jump beyond this is a different column, not a restatement.
MAX_RESTATEMENT = 0.10

def _period_headed(document: Document, locator: dict) -> bool:
    """Whether a value's column is headed by words naming its period ("three months ended", "Q4"), so the row's
    other cells under the same words are the same length of period."""
    table = document.tables.get(str(locator.get("table")))
    if table is None or locator.get("col") is None:
        return False
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0])), 1)
    return bool(_PERIOD_HEADER.search(column_header(table, first, int(locator["col"]))))

_PERIOD_HEADER = re.compile(r"months?|weeks?|quarter|year|\bq[1-4]\b|\bfy\b|@", re.I)

def _proven(value: dict) -> bool:
    # A line replaced by its report's exact tagged figure (Adobe's customer groups) is a figure reported in XBRL.
    return any(note.startswith(_PROOFS) or _EXACT_NOTE.match(note) for note in value.get("notes") or [])



# Holds a row gets from its group alone: the breakdown's sum, its total's anchor, or another row's hold.
_GROUP_HOLDS = ("parts differ from the total by", "another part of this breakdown needs review",
                "total matches no figure the company reported in XBRL", "no total revenue to reconcile against")
_ASIDE = ("confirmed by review", "settled by the checks", "checked against ")


def ties_out(value: dict, year_ago: float | None) -> bool:
    """Whether the value's own row, as printed, gives the figure already accepted for the quarter a year before, in the
    value's units: two filings agree on the row, its scale and its series."""
    numbers = (value.get("locator") or {}).get("row_numbers") or []
    if year_ago is None or len(numbers) < 2 or not numbers[0]:
        return False
    factor = float(value["value"]) / float(numbers[0])
    return any(abs(float(n) * factor - year_ago) <= max(abs(year_ago) * 1e-4, 1e-9) for n in numbers[1:])


def _overlaps(rows: list[dict]) -> list[dict]:
    """Rows of one breakdown that count the same money twice: a run of adjacent rows of one table that adds up, to the
    table's rounding, to another row of the breakdown, and that row. PNC's "Other noninterest income" (214) is its
    Visa gains (11), securities losses (-1) and "Other" (204) together."""
    placed = sorted((v for v in rows if (v.get("locator") or {}).get("table") and (v.get("locator") or {}).get("row") is not None),
                    key=lambda v: (str(v["locator"]["table"]), int(v["locator"]["row"])))
    found: list[dict] = []
    for start in range(len(placed)):
        run: list[dict] = []
        total = 0.0
        for v in placed[start:]:
            if run and (v["locator"]["table"] != run[-1]["locator"]["table"]
                        or int(v["locator"]["row"]) != int(run[-1]["locator"]["row"]) + 1):
                break
            run.append(v)
            total += float(v["value"])
            if len(run) < 2:
                continue
            unit = max(float(r["locator"].get("scale") or 1) for r in run)
            found += [item for other in rows if all(other is not r for r in run) and float(other["value"])
                      and abs(float(other["value"]) - total) <= unit * (len(run) + 1) / 2 for item in (*run, other)]
    return found


def tied_rows(values: list[dict]) -> list[dict]:
    """Breakdown rows held only for their group (parts off the total, a total no XBRL figure matches, another row's
    hold) whose own row ties out to last year's accepted figure: UCB's fee lines beside an unlisted remainder, Henry
    Schein's U.S. products, PNC's fee types. A row a second KPI shares (Axia's "Contractual" twice) never stands alone,
    and a row held for itself (its scale, a jump, its period, a place, a twin row) keeps its hold. Oldest first, so a
    row cleared this way is last year's figure for the next."""
    def group_held_only(v: dict) -> bool:
        own = [note for note in v.get("notes") or [] if not note.startswith((*_ASIDE, *_PROOFS)) and not _EXACT_NOTE.match(note)]
        return all(note.startswith(_GROUP_HOLDS) for note in own)

    # Last year's figure counts when it is accepted, or held only for its group and read from another filing: two
    # filings then agree on the row (PNC's fee types, held every year because their total is no XBRL figure).
    accepted = {(v["group_key"], v["kpi_key"], v["fiscal_period"] == "FY", v["period_end"]): (float(v["value"]), v.get("source_accession"))
                for v in values if v["validation_status"] == "verified"
                or v["validation_status"] == "needs_review" and v["method"] == "ai" and group_held_only(v)}
    released = []
    by_period: dict[tuple, list[dict]] = {}
    for v in values:
        if v["method"] == "ai" and v["group_kind"] != "metric" and v["validation_status"] != "rejected":
            by_period.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), []).append(v)
    # Rows that count the same money twice never stand alone, the subtotal nor its lines.
    overlapping = {id(v) for rows in by_period.values() for v in _overlaps(rows)}
    for v in sorted(values, key=lambda v: v["period_end"]):
        if v["method"] != "ai" or v["group_kind"] == "metric" or v["validation_status"] != "needs_review":
            continue
        # A note that proves or refines the figure (confirmed by the next year's release, the 10-Q's exact figure) is no hold.
        if not group_held_only(v) or id(v) in overlapping:
            continue
        locator = v.get("locator") or {}
        siblings = [o for o in by_period.get((v["group_key"], v["fiscal_year"], v["fiscal_period"]), []) if o is not v]
        if any(abs(float(o["value"]) - float(v["value"])) <= abs(float(v["value"])) * 1e-9
               or ((o.get("locator") or {}).get("table"), (o.get("locator") or {}).get("row")) == (locator.get("table"), locator.get("row"))
               and locator.get("table") for o in siblings):
            continue
        end = date.fromisoformat(v["period_end"][:10])
        year_ago = next((figure for (g, k, annual, other_end), (figure, filing) in accepted.items()
                         if g == v["group_key"] and k == v["kpi_key"] and annual == (v["fiscal_period"] == "FY")
                         and abs((end - timedelta(days=365) - date.fromisoformat(other_end[:10])).days) <= 12
                         and filing != v.get("source_accession")), None)
        if ties_out(v, year_ago) or _CONFIRMED_LATER in (v.get("notes") or []):
            cleared = {**v, "validation_status": "verified", "notes": [*(v.get("notes") or []), TIES_OUT]}
            released.append(cleared)
            accepted[(v["group_key"], v["kpi_key"], v["fiscal_period"] == "FY", v["period_end"])] = (float(v["value"]), v.get("source_accession"))
    return released


class SeriesChecks:
    """Checks across periods: proving or clearing flagged figures, scale runs, and working out derived periods."""

    def _stand_tied_rows(self) -> list[dict]:
        """Breakdown rows held only for their group whose own row ties out to last year's figure (tied_rows)."""
        return tied_rows(list(self.values.values()))

    def _anchor_ai_breakdowns(self) -> list[dict]:
        """An AI breakdown's total must be a figure the company reported in XBRL for the same period: its revenue, or a
        segment it splits (AMD's "Client and Gaming"). Checked every run, so a release read before its 10-Q arrived is
        checked once it has. A breakdown that matches nothing reported is flagged, all of it."""
        # (period end, figure, the member it is when it is one segment rather than revenue)
        reported: dict[str, list[tuple[str, float, str | None]]] = {}
        for v in self.values.values():
            if v["method"] in ("xbrl", "derived") and not v["group_key"].startswith(AI_GROUP_PREFIX) \
                    and v["validation_status"] == "verified":
                figures = [(float(v["value"]), v["kpi_label"])]
                if (v.get("locator") or {}).get("total"):
                    figures.append((float(v["locator"]["total"]), None))
                reported.setdefault(v["fiscal_period"], []).extend((v["period_end"], f, member) for f, member in figures)
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if (v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] == "verified"
                    and (v.get("locator") or {}).get("total")):
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), []).append(v)
        # Parts each proven on their own stand, whatever their read total row says; so does a breakdown whose year-ago
        # column repeats last year's release line for line (two filings agree on the table).
        groups = {key: rows for key, rows in groups.items()
                  if not all(_proven(v) for v in rows) and not all((v.get("locator") or {}).get("year_ago_agrees") for v in rows)}
        flagged = []
        segment_of: dict[str, set[str]] = {}
        for (group, _year, period), rows in groups.items():
            total, end = float(rows[0]["locator"]["total"]), rows[0]["period_end"]
            any_reported, matches = reported_matches(total, end, reported.get(period, []))
            if any_reported and not matches:
                # A row proven on its own (its year-ago figure, the next year's release) stands whatever the total.
                flagged += [{**v, "validation_status": "needs_review",
                             "notes": [*(v.get("notes") or []), "total matches no figure the company reported in XBRL"]}
                            for v in rows if not _proven(v)]
            elif matches and all(member for member in matches):
                segment_of.setdefault(group, set()).add(matches[0])
        # A breakdown of one segment (AMD's Client + Gaming = its "Client and Gaming" segment) is titled for that
        # segment, so it never reads as company-wide revenue by product.
        for group, members in segment_of.items():
            if len(members) != 1:
                continue
            member = members.pop()
            for v in self.values.values():
                if v["group_key"] == group and v["validation_status"] != "rejected" and member not in v["group_label"]:
                    flagged.append({**v, "group_label": f"{member} {v['group_label']}"[:80]})
        return flagged

    def _prove_flagged(self, releases: list[FilingRef], parents: dict[tuple[str, str], str] | None = None) -> list[dict]:
        """Settles flagged release values by evidence, with no AI and no one reviewing them:
        - a value equal to a figure the company reported in XBRL for the same period is proven;
        - a breakdown whose parts add up to such a figure is proven whole;
        - the next year's release repeats each figure in its year-ago column: the same number proves it, a single other
          number is the figure as restated and replaces it, and none matching among several rejects it.
        What nothing settles stays unserved. A later release is fetched for a value only until it has been checked."""
        flagged = [v for v in self.values.values() if v["method"] == "ai" and v["validation_status"] == "needs_review"]
        official: dict[bool, list[tuple[str, float]]] = {}
        for v in self.values.values():
            if (v["method"] in ("xbrl", "derived") and not v["group_key"].startswith(AI_GROUP_PREFIX)
                    and v["validation_status"] == "verified"):
                figures = official.setdefault(v["fiscal_period"] == "FY", [])
                figures.append((v["period_end"], float(v["value"])))
                if (v.get("locator") or {}).get("total"):
                    figures.append((v["period_end"], float(v["locator"]["total"])))

        def reported(row: dict, figure: float, tolerance: float) -> bool:
            return any(abs(_days(end, row["period_end"])) <= 12 and abs(other - figure) <= abs(figure) * tolerance
                       for end, other in official.get(row["fiscal_period"] == "FY", []))

        def settle(row: dict, note: str, **fields) -> None:
            settled[value_key(row)] = {**row, "validation_status": "verified", **fields,
                                       "notes": [*(row.get("notes") or []), note]}

        settled: dict[tuple, dict] = {}
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)

        def whole(row: dict) -> bool:
            """A breakdown part counts as proven only in a breakdown that adds up to its own total: every visible part
            can match XBRL while a part the release left out (UnitedHealth's eliminations) still makes it incomplete."""
            if row["group_kind"] != "revenue_breakdown":
                return True
            rows = groups.get((row["group_key"], row["fiscal_year"], row["fiscal_period"], row.get("source_accession")), [])
            total = float((row.get("locator") or {}).get("total") or 0)
            return not total or abs(sum(float(o["value"]) for o in rows) - total) <= abs(total) * 0.001

        for v in flagged:
            value = float(v["value"])
            if v["unit"] == "currency" and _significant(value) and reported(v, value, 1e-4) and whole(v):
                settle(v, _EQUALS_XBRL)
        # A restatement far from the figure it replaced was a column of another length (a year for a quarter): the
        # figure is restored and goes back to review.
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "verified":
                continue
            note = next((n for n in v.get("notes") or [] if n.startswith(_RESTATED_LATER)), None)
            match = re.search(r"\(was ([-\d.e+]+)\)", note or "")
            if match and abs(float(v["value"]) - float(match.group(1))) > abs(float(match.group(1))) * MAX_RESTATEMENT:
                settled[value_key(v)] = {**v, "value": float(match.group(1)), "validation_status": "needs_review",
                                         "notes": [n for n in v.get("notes") or [] if n != note]}
        # Parts proven alone by an earlier version of this rule, in breakdowns that do not add up, go back to review.
        for rows in groups.values():
            if not whole(rows[0]):
                for v in rows:
                    if v["validation_status"] == "verified" and {_EQUALS_XBRL, _ADDS_TO_XBRL} & set(v.get("notes") or []):
                        settled[value_key(v)] = {**v, "validation_status": "needs_review", "notes": [
                            note for note in v.get("notes") or [] if note not in (_EQUALS_XBRL, _ADDS_TO_XBRL)]}
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review" and value_key(v) not in settled]
            # To the figures' own rounding: Unity's release gives its Q3 2024 lines to the million ($447 million in all),
            # its XBRL revenue to the thousand ($446.5 million).
            parts = sum(float(v["value"]) for v in rows)
            rounding = sum(_rounding(float(v["value"])) for v in rows) / abs(parts) if parts else 0
            if waiting and len(rows) >= 2 and whole(rows[0]) and reported(rows[0], parts, max(1e-5, min(rounding, 0.005))):
                for v in waiting:
                    settle(v, _ADDS_TO_XBRL)
        # A split of part of a line (Adobe's customer groups of its subscription revenue, with their own total that XBRL
        # never tags) is proven by a reported figure it nearly fills, when its parts add up to its stated total to their
        # rounding and nothing else is wrong with it.
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review" and value_key(v) not in settled]
            if len(rows) < 2 or not waiting or any(not {n for n in v.get("notes") or [] if not _EXACT_NOTE.match(n)} <= _UNANCHORED
                                                   for v in waiting):
                continue
            parts = [float(v["value"]) for v in rows]
            stated = float((rows[0].get("locator") or {}).get("total") or 0) or sum(parts)
            if abs(sum(parts) - stated) > sum(_rounding(value) for value in [*parts, stated]):
                continue
            if any(abs(_days(end, rows[0]["period_end"])) <= 12 and _MIN_COVERAGE * other <= stated <= other
                   for end, other in official.get(rows[0]["fiscal_period"] == "FY", [])):
                for v in waiting:
                    settle(v, _WITHIN_XBRL)
        # A breakdown period takes the next year's restated figures whole or not at all: a part the next release no longer
        # shows (TSMC's 16/20nm, split in two from 2019) left beside restated parts made Q3 2018 add up to neither total.
        restatements: dict[tuple, list[tuple[dict, float, str]]] = {}

        def period_of(row: dict) -> tuple:
            return (row["group_key"], row["fiscal_year"], row["fiscal_period"])

        def ai_parts(row: dict) -> list[dict]:
            return [o for o in self.values.values() if period_of(o) == period_of(row)
                    and o["method"] == "ai" and o["validation_status"] != "rejected"]

        # Parts restated alone before this rule, beside parts that were not: the period goes back to its own figures.
        for v in self.values.values():
            if v["method"] != "ai" or v["group_kind"] != "revenue_breakdown" or v["validation_status"] == "rejected":
                continue
            note = next((n for n in v.get("notes") or [] if n.startswith(_RESTATED_LATER)), None)
            match = re.search(r"\(was ([-\d.e+]+)\)", note or "")
            if match and not all(any(n.startswith(_RESTATED_LATER) for n in o.get("notes") or []) for o in ai_parts(v)):
                settled[value_key(v)] = {**v, "value": float(match.group(1)), "validation_status": "needs_review",
                                         "notes": [n for n in v.get("notes") or [] if n != note]}
        by_accession = {ref.accession: ref for ref in releases}
        for v in flagged:
            if value_key(v) in settled:
                continue
            target = (date.fromisoformat(v["period_end"][:10]) + timedelta(days=365)).isoformat()
            later = next((o for o in self.values.values()
                          if o["method"] == "ai" and o["validation_status"] == "verified"
                          and o["group_key"] == v["group_key"] and o["kpi_key"] == v["kpi_key"]
                          and (o["fiscal_period"] == "FY") == (v["fiscal_period"] == "FY")
                          and abs(_days(o["period_end"], target)) <= 12 and o.get("source_accession") in by_accession
                          and (o.get("locator") or {}).get("row") is not None), None)
            if later is None:
                continue
            checked = f"checked against {later['source_accession']}"
            if checked in (v.get("notes") or []):
                continue
            try:
                document = self._document(by_accession[later["source_accession"]])
            except Exception:  # noqa: BLE001 - an unreadable release settles nothing
                continue
            cells = _year_ago_cells(document, later["locator"], float(later["value"]))
            value = float(v["value"])
            named = _period_headed(document, later["locator"])
            if any(abs(cell - value) <= max(abs(value) * 1e-4, 1e-9) for cell in cells):
                settle(v, _CONFIRMED_LATER)
            elif named and len(cells) == 1 and abs(cells[0] - value) <= abs(value) * MAX_RESTATEMENT:
                if v["group_kind"] == "revenue_breakdown":
                    restatements.setdefault(period_of(v), []).append((v, round(cells[0], 2), checked))
                else:
                    settle(v, f"{_RESTATED_LATER} (was {value:.15g})", value=round(cells[0], 2))
            elif named and len(cells) > 1:
                settled[value_key(v)] = {**v, "validation_status": "rejected",
                                         "notes": [*(v.get("notes") or []), "contradicted by the next year's release"]}
            else:
                settled[value_key(v)] = {**v, "notes": [*(v.get("notes") or []), checked]}
        for period, items in restatements.items():
            restated = {value_key(row) for row, _figure, _checked in items}
            parts = ai_parts(items[0][0])
            if all(value_key(o) in restated or _CONFIRMED_LATER in ((settled.get(value_key(o)) or o).get("notes") or [])
                   for o in parts):
                for row, figure, _checked in items:
                    settle(row, f"{_RESTATED_LATER} (was {float(row['value']):.15g})", value=figure)
            else:
                # Kept as filed; recorded as checked so the next release is not read for it again.
                for row, _figure, checked in items:
                    settled[value_key(row)] = {**row, "notes": [*(row.get("notes") or []), checked]}
        # Every line confirmed by the next year's release says nothing of the lines the reading missed: Goldman's Q3 2024
        # Global Banking & Markets, its Equities intermediation unread, stood $2.2 billion short of its total.
        periods: dict[tuple, list[dict]] = {}
        for rows in groups.values():
            periods.setdefault(period_of(rows[0]), []).extend(rows)  # an owner's entry cites another filing of the quarter
        for rows in periods.values():
            period = [row for row in (settled.get(value_key(v)) or v for v in rows) if row["validation_status"] != "rejected"]
            if not any(_CONFIRMED_LATER in (row.get("notes") or []) for row in period) or parts_add_up(period, parents):
                continue
            for row in period:
                if row["validation_status"] == "verified" and not row.get("owner_decision"):
                    settled[value_key(row)] = {**row, "validation_status": "needs_review", "notes": (
                        row.get("notes") or [] if _CONFIRMED_LATER in (row.get("notes") or [])
                        else [*(row.get("notes") or []), "another part of this breakdown needs review"])}
        # A breakdown or mix is proven whole or not at all: a part confirmed on its own, beside parts still flagged, stays
        # flagged with them (its confirmation note kept for when the rest is settled).
        for key, row in list(settled.items()):
            if row["validation_status"] != "verified" or row["group_kind"] == "metric":
                continue
            siblings = [v for v in self.values.values() if v["group_key"] == row["group_key"]
                        and v["fiscal_year"] == row["fiscal_year"] and v["fiscal_period"] == row["fiscal_period"]
                        and v["method"] == "ai" and v["validation_status"] != "rejected"]
            if any((settled.get(value_key(v)) or v)["validation_status"] != "verified" for v in siblings):
                settled[key] = {**row, "validation_status": "needs_review"}
        changed = list(settled.values())
        proven = sum(1 for v in changed if v["validation_status"] == "verified")
        if proven:
            self.log(f"{self.symbol}: {proven} flagged values proven by later or official figures")
        return changed

    def _q4_gaps(self) -> list[str]:
        periods: dict[tuple[str, str], set[str]] = {}
        for v in self.values.values():
            if v["group_kind"] == "revenue_breakdown" and v["validation_status"] == "verified":
                periods.setdefault((v["group_label"], v["fiscal_year"]), set()).add(v["fiscal_period"])
        return sorted(f"{label} {year}" for (label, year), found in periods.items()
                      if {"FY", "Q1", "Q2", "Q3"} <= found and "Q4" not in found)

    def _recheck_flagged(self) -> list[dict]:
        """Clears flags that stored figures themselves disprove, without reading anything again:
        - a full year flagged for jumping against a quarter is compared with the year before instead;
        - a table read whole without its total row reconciles to a figure the same filing reports for the same period
          (TSMC's geography to its net revenue, its nodes to wafer revenue), to within 0.001%."""
        cleared = []
        by_filing: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] != "rejected":
                by_filing.setdefault((v.get("source_accession") or "", v["period_end"]), []).append(v)
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "needs_review" or v["fiscal_period"] != "FY":
                continue
            notes = v.get("notes") or []
            if notes and all(_is_jump(note) and note.endswith("in a quarter") for note in notes):
                key = f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}"
                before = self._previous(date.fromisoformat(v["period_end"][:10]), annual=True).get(key)
                siblings = by_filing.get((v.get("source_accession") or "", v["period_end"]), [])
                parts = sum(float(o["value"]) for o in siblings if o["group_key"] == v["group_key"])
                total = float((v.get("locator") or {}).get("total") or 0)
                if total and abs(parts - total) <= abs(total) * 0.001:
                    # A new node ramping 3.5× in a year is growth; the year's table adding up exactly is the check.
                    cleared.append({**v, "validation_status": "verified", "notes": ["the year's breakdown reconciles"]})
                elif before is None or 1 / MAX_YEAR_RATIO <= float(v["value"]) / before <= MAX_YEAR_RATIO:
                    cleared.append({**v, "validation_status": "verified", "notes": ["checked against the prior year"]})
        for (accession, _end), rows in by_filing.items():
            for group_key in {v["group_key"] for v in rows if v["group_kind"] == "revenue_breakdown"}:
                members = [m for m in rows if m["group_key"] == group_key]
                total = sum(float(m["value"]) for m in members)
                for m in members:
                    notes = m.get("notes") or []
                    if (m["validation_status"] == "needs_review" and notes and all(_is_jump(note) for note in notes)
                            and total and abs(float(m["value"])) < 0.01 * abs(total)):
                        cleared.append({**m, "validation_status": "verified", "notes": ["immaterial row; the total checks it"]})
            groups: dict[str, list[dict]] = {}
            for v in rows:
                groups.setdefault(v["group_key"], []).append(v)
            for group_key, members in groups.items():
                if not all(m["validation_status"] == "needs_review" and (m.get("locator") or {}).get("whole_table")
                           and (m.get("notes") or []) == ["no total revenue to reconcile against"] for m in members):
                    continue
                parts = sum(float(m["value"]) for m in members)
                anchors = [float(o["value"]) for o in rows if o["group_key"] != group_key and o["validation_status"] == "verified"]
                anchors += [float((o.get("locator") or {}).get("total")) for o in rows
                            if o["group_key"] != group_key and o["validation_status"] == "verified" and (o.get("locator") or {}).get("total")]
                if len(members) >= 2 and any(abs(parts - anchor) <= abs(anchor) * 1e-5 for anchor in anchors):
                    cleared += [{**m, "validation_status": "verified", "locator": {**m["locator"], "total": parts},
                                 "reconciliation_error_pct": 0, "notes": ["reconciled to another figure in the same report"]}
                                for m in members]
        cleared += self._scale_runs()
        cleared += self._outdated_flags()
        return cleared

    def _scale_runs(self) -> list[dict]:
        """A run of a metric's figures read without their table's scale (Visa's "Processed Transactions (millions)",
        29,321 for 29.3 billion from Q2 2018 to Q3 2020): the series steps by almost exactly a thousand, a million or a
        billion where the run starts and ends, so it splits into stretches by those steps, and the stretches off the
        scale most of its figures share are put back on it. Only the run's edges jump, so a jump check alone flags two
        quarters and leaves the run between them; an owner's figure is never changed, and percentages have no scale."""
        series: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            # A per-unit amount has no table scale to put back: FedEx's $15.32 composite yield, read right, was raised
            # a thousandfold to match quarters read wrongly with the table's "in thousands".
            if (v["method"] == "ai" and v["group_kind"] == "metric" and v["validation_status"] != "rejected"
                    and v["unit"] != "percent" and float(v["value"]) > 0 and not _PER_UNIT_KPI.search(v.get("kpi_label") or "")):
                series.setdefault((v["group_key"], v["kpi_key"], v["fiscal_period"] == "FY"), []).append(v)
        records = []
        for rows in series.values():
            rows.sort(key=lambda v: (v["period_end"], v["fiscal_period"]))
            levels, level = [], 0
            for previous, current in zip([None, *rows], rows):
                if previous is not None:
                    step = math.log10(float(current["value"]) / float(previous["value"]))
                    thousands = round(step / 3)
                    if thousands and abs(step - 3 * thousands) < 0.3:
                        level += thousands
                levels.append(level)
            counts = {item: levels.count(item) for item in set(levels)}
            if len(counts) < 2:
                continue
            main = max(counts, key=lambda item: (counts[item], -abs(item)))
            if counts[main] * 2 <= len(rows):
                continue  # no scale most figures share
            for index, (v, at) in enumerate(zip(rows, levels)):
                if at == main and v["validation_status"] == "needs_review" and index and levels[index - 1] != main \
                        and all(_is_jump(note) or note == "confirmed by review" for note in v.get("notes") or []):
                    # Its jump was from the run before it.
                    records.append({**v, "validation_status": "verified", "notes": ["the figure before it was off its scale"]})
                if at == main or v.get("owner_decision"):
                    continue
                factor = 10 ** (3 * (main - at))
                notes = [note for note in v.get("notes") or [] if not _is_jump(note) and note != "confirmed by review"]
                records.append({**v, "value": round(float(v["value"]) * factor, 6),
                                "validation_status": "verified" if not notes else v["validation_status"],
                                "notes": [*notes, f"read without its table's scale: {factor:g}× to match the series"]})
        if records:
            self.log(f"{self.symbol}: {len(records)} figures put back on their series' scale")
        return records

    def _outdated_flags(self) -> list[dict]:
        """Flags set by a check that today's checks no longer raise, cleared from the stored figures alone:
        - a small count's ratio jump (ASML's used systems sold, 10 to 3), flagged before small counts moved by units;
        - a breakdown's parts off their total (ASML's 2023 quarters, read twice over while two keys named each line),
          now that its parts add up to the release's stated total."""
        aside = ("confirmed by review", "checked against ")
        cleared = []
        series: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] == "verified" and v["fiscal_period"] != "FY":
                series.setdefault((v["group_key"], v["kpi_key"]), []).append(v)
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] == "revenue_breakdown" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "needs_review" or v["fiscal_period"] == "FY":
                continue
            reasons = [note for note in v.get("notes") or [] if not note.startswith(aside)]
            if v["unit"] != "count" or not reasons or not all(note.startswith("changed ") for note in reasons):
                continue
            before = max((o for o in series.get((v["group_key"], v["kpi_key"]), [])
                          if 0 < _days(v["period_end"], o["period_end"]) <= 120), key=lambda o: o["period_end"], default=None)
            if before is not None and count_jump(v["unit"], float(before["value"]), float(v["value"])) == "":
                cleared.append({**v, "validation_status": "verified",
                                "notes": [*(v.get("notes") or []), "a small count's move, within the rule for small counts"]})
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review"]
            stated = float((rows[0].get("locator") or {}).get("total") or 0)
            if not waiting or len(rows) < 2 or not stated or len(waiting) != len(rows):
                continue
            if not all([note for note in v.get("notes") or [] if not note.startswith(aside)]
                       and all(note.startswith("parts differ from the total by") for note in v.get("notes") or []
                               if not note.startswith(aside)) for v in waiting):
                continue
            parts = [float(v["value"]) for v in rows]
            if abs(sum(parts) - stated) <= abs(stated) * 0.001:
                cleared += [{**v, "validation_status": "verified", "reconciliation_error_pct": (sum(parts) - stated) / stated,
                             "notes": [*(v.get("notes") or []), "the parts add up to the release's total"]} for v in rows]
        return cleared

    def _release_held_breakdowns(self) -> list[dict]:
        """Parts flagged only because another part of their breakdown was, released once that part is cleared: TSMC's
        FY2024 nodes were held for 3nm's 3.5× ramp, which the year's reconciling table then confirmed."""
        held = "another part of this breakdown needs review"
        groups: dict[tuple, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["group_kind"] != "metric" and v["validation_status"] != "rejected":
                groups.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"], v.get("source_accession")), []).append(v)
        released = []
        for rows in groups.values():
            waiting = [v for v in rows if v["validation_status"] == "needs_review"]
            if waiting and all(set(v.get("notes") or []) <= {held, "confirmed by review"} and held in (v.get("notes") or [])
                               for v in waiting):
                released += [{**v, "validation_status": "verified",
                              "notes": [note for note in (v.get("notes") or []) if note != held]} for v in waiting]
        return released

    def _reports_full_year(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown in this document adds up to the fiscal year's revenue in XBRL (an annual report the
        AI took for a quarter), whatever the AI said."""
        annual_totals = [
            float(value["locator"]["total"]) for value in self.values.values()
            if value["method"] == "xbrl" and value["fiscal_period"] == "FY" and (value.get("locator") or {}).get("total")
            and abs(_days(value["period_end"], period_end.isoformat())) <= 12
        ]
        if not annual_totals:
            return False
        sums: dict[str, float] = {}
        for item in read:
            if item.group.kind != "revenue_breakdown":
                continue
            if item.kpi.key == item.group.total_kpi:
                sums[item.group.key] = item.value
            elif item.group.total_kpi is None:
                sums[item.group.key] = sums.get(item.group.key, 0.0) + item.value
        return any(abs(total - annual) <= abs(annual) * 0.01 for total in sums.values() for annual in annual_totals)

    def _earlier_values(self, before: date) -> dict[str, list[float]]:
        earlier: dict[str, list[float]] = {}
        chained = getattr(self._local, "chained", False)
        for value in list(self.values.values()):
            if chained and not _same_chain(value["period_end"], before):
                continue  # the other chains' quarters are compared after reading, in date order
            if value["method"] == "ai" and value["period_end"] < (before - timedelta(days=20)).isoformat():
                key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
                earlier.setdefault(key, []).append(float(value["value"]))
        return earlier

    def _previous(self, before: date, annual: bool = False) -> dict[str, float]:
        """The latest verified value of each AI KPI in the period before, for jump checks: the quarter before a
        quarter, the year before a year (a year is never compared with a quarter)."""
        if getattr(self._local, "chained", False) and not annual:
            return {}  # quarters read in chains are compared with the quarter before after reading (_check_read_series)
        window_start = (before - timedelta(days=400 if annual else 120)).isoformat()
        latest: dict[str, dict] = {}
        for value in self.values.values():
            if (value["method"] != "ai" or value["validation_status"] != "verified"
                    or (value["fiscal_period"] == "FY") != annual
                    or not window_start <= value["period_end"] < before.isoformat()):
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] > latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        return {key: float(value["value"]) for key, value in latest.items()}

    def _default_currency(self) -> str | None:
        """The company's reporting currency: its tagged figures' first, else the one most of its other figures carry, so
        a bank with no tagged segments (Stock Yards, Meridian) still reads its unmarked amounts as dollars."""
        currencies = [value.get("currency") for value in self.values.values() if value["method"] == "xbrl"]
        if tagged := next((currency for currency in reversed(currencies) if currency), None):
            return tagged
        others = Counter(value.get("currency") for value in self.values.values() if value.get("currency"))
        return others.most_common(1)[0][0] if others else None

    def _derive(self, spec: Spec | None = None) -> list[dict]:
        changed = []
        # A template KPI that is a line of another (Apollo's Credit and Equity management fees, lines of its management
        # fees) carries that parent, so a worked-out period is checked on its top level only.
        parents = list_parents(spec)
        values = [{**v, "locator": {**(v.get("locator") or {}), "parent": parents[(v["group_key"], v["kpi_key"])]}}
                  if (v["group_key"], v["kpi_key"]) in parents else v for v in self.values.values()]
        derived = [{**record, "locator": {k: v for k, v in (record.get("locator") or {}).items() if k != "parent"} or None}
                   if (record["group_key"], record["kpi_key"]) in parents else record for record in derive_periods(values)]
        produced = {value_key(record) for record in derived}
        # Derived values whose inputs are gone or were replaced (e.g. a Q4 from a rejected reading) are withdrawn.
        changed.extend({**value, "validation_status": "rejected", "notes": ["inputs no longer available"]}
                       for key, value in self.values.items()
                       if value["method"] == "derived" and value["validation_status"] != "rejected" and key not in produced)
        for record in derived:
            existing = self.values.get(value_key(record))
            if existing and existing["method"] != "derived" and existing["validation_status"] == "verified":
                continue  # a reported figure wins; a flagged partial reading (TSMC's release percentages) does not
            if existing and same_derivation(existing, record):
                continue
            changed.append(record)
        return changed
