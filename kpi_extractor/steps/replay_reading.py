"""Code reads first: each group read again, with no AI, the way the nearest quarter of the same kind of document was
read; a metric group's KPIs one by one, since nothing adds them up. A reading stands only on the checks a Flash-only
reading must pass. What code leaves goes to the AI, and each group (or metric KPI) the AI reads instead says why, in
the run summary's replayOutcomes: the learning loop's measure of where code should learn next."""
from __future__ import annotations

import copy
import re
from datetime import date, timedelta

from ..ai import GroupSpec, Located, Spec
from ..documents.document import Document, parse_number
from ..domain.fiscal import fiscal_label
from ..readers.extract import ReadValue, read_values
from ..readers.anchor import KIND as ANCHOR, anchor_locator
from ..readers.quoted_rows import row_line
from ..readers.replay import (AmbiguousQuote, _same_place, _source_template, chosen_by_number, comparable_header,
                              missing_since, replay, reported_period, stated_total)
from ..ops.learned import LIVE, SHADOW, evidence, spot_checked
from ..quality.checks import check_period, validate_groups
from ..readers.trace import placed_elsewhere, trace_replay
from .shared import (AI_GROUP_PREFIX, _ANNUAL_HEADER, _NOT_REPORTED, _QUARTER_HEADER, _READ_BY_AI, _annual_header, _days, _same,
                     _same_chain, _year_ago_cells)

REPLAYED = "replayed"


def kpi_by_kpi(group: GroupSpec) -> bool:
    """A metric group whose KPIs stand alone (no total, no lines of a parent): one KPI code cannot find again sends only
    that KPI to the AI, not the whole group."""
    return group.kind == "metric" and not group.total_kpi and not any(kpi.parent for kpi in group.kpis)


def left_for_ai(spec: Spec, done: set[str]) -> Spec | None:
    """The part of the list code did not read: groups not replayed, and of a metric group only the KPIs not replayed.
    None when code read it all."""
    groups = [{**group.model_dump(), "kpis": kpis} for group in spec.groups if group.key not in done
              if (kpis := [kpi.model_dump() for kpi in group.kpis if f"{group.key}.{kpi.key}" not in done])]
    return Spec.model_validate({"groups": groups, "names": {}}) if groups else None



def in_full_groups(items: list[ReadValue], spec: Spec) -> list[ReadValue]:
    """Values read for part of a group (one metric KPI code replayed, or the ones it left to the AI) carry the KPI list's
    own group and KPI again, as every later step expects: the release flow orders groups by their place in the list."""
    groups = {group.key: group for group in spec.groups}
    for item in items:
        if (group := groups.get(item.group.key)) is not None and group is not item.group:
            item.group = group
            item.kpi = next((kpi for kpi in group.kpis if kpi.key == item.kpi.key), item.kpi)
    return items


class ReplayReading:
    def _replay_groups(self, document: Document, spec: Spec, expected: date, currency: str | None):
        """Each group (each KPI of a metric group) read again by code from where it was found in the nearest quarter of
        the same kind of document, kept only when it passes every check a Flash-only reading must (its period, its sums,
        no jumps, nothing found before missing, every value where it was). A KPI the list has but the nearest reading did
        not find is looked for by the AI unless that reading's AI read noted it as not reported. Returns the kept values,
        the first kept reading, and the keys read: a group's key, or "group.kpi" for a metric KPI."""
        source = _source_template(document.source_url)
        last = self._last_locators(expected, source)
        self._local.places = None
        outcomes = self.result.replay_outcomes

        def missed(why: str, times: int = 1) -> None:
            if times:
                outcomes[why] = outcomes.get(why, 0) + times
        if not last:
            missed("no earlier reading of this kind of document", len(spec.groups))
            return self._replay_year(document, spec, expected, currency) or ([], None, set())
        history = self._kind_history(expected, source)
        if all((history.get(key) or {}).get("fiscal_period") == "FY" for key in last):
            # The newest reading of this kind was the year's (TSMC's Q4 annual report): a quarter replays from the latest
            # quarter's places, the same layout ("three months ended"), not the year's columns.
            quarter_history = self._kind_history(expected, source, quarters=True)
            newest = max((value["period_end"] for value in quarter_history.values()), default=None)
            quarter_last = {key: value["locator"] for key, value in quarter_history.items()
                            if value["period_end"] == newest and value.get("locator") and value["validation_status"] == "verified"}
            if quarter_last:  # else the year's places stay, and are left to the AI as before
                history, last = quarter_history, quarter_last
        # A metric KPI the newest reading of this kind lacks (missed there, not reported that quarter, or left to the AI)
        # is looked for where an earlier quarter of this kind found it, within the year: one quarter's miss no longer
        # leaves it without a place for good. Only KPIs read one by one, never a year's place; every check still holds.
        groups = {group.key: group for group in spec.groups}
        oldest = (expected - timedelta(days=400)).isoformat()
        last = {**last, **{key: value["locator"] for key, value in history.items()
                           if key not in last and (group := groups.get(key.split(".", 1)[0])) is not None and kpi_by_kpi(group)
                           and value.get("locator") and value["validation_status"] == "verified"
                           and value["fiscal_period"] != "FY" and value["period_end"] >= oldest}}
        self._local.places = last
        accession = next((value.get("source_accession") for key, value in history.items() if key in last), None)
        not_reported = self._not_reported_before(accession, expected)
        items: list[ReadValue] = []
        kept_located, done = None, set()
        for group in spec.groups:
            mine = {key: locator for key, locator in last.items() if key.split(".", 1)[0] == group.key}
            if not mine:
                missed("group not read in this kind of document before")
                continue
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis if kpi.key != group.total_kpi}
            unlooked = (listed - mine.keys()) - not_reported  # nobody has looked for it in this kind of document yet
            # A place last read as the year's figure holds a year, and a replay states no period of its own: TSMC's 2023
            # wafer shipments (12,002,177, its annual statements' row) were replayed as Q4's. The AI reads the period.
            yearly = {key for key in mine if (history.get(key) or {}).get("fiscal_period") == "FY"}
            missed("last reading was the year's", len(yearly))
            if yearly and not kpi_by_kpi(group):
                continue
            if kpi_by_kpi(group):
                parts = [({key: locator}, Spec.model_validate({"groups": [{**group.model_dump(), "kpis": [
                    kpi.model_dump() for kpi in group.kpis if f"{group.key}.{kpi.key}" == key]}]}), key)
                    for key, locator in mine.items() if key in listed and key not in yearly]
                missed("a listed KPI not looked for yet", len(unlooked))
            elif unlooked and not any(locator.get("whole_table") for locator in mine.values()):
                missed("a listed KPI not looked for yet")
                continue
            else:
                parts = [(mine, Spec.model_validate({"groups": [group.model_dump()]}), group.key)]
            for locators, part, key in parts:
                found = self._replay_part(document, part, locators, expected, currency)
                if isinstance(found, str) and kpi_by_kpi(group) and "quote" in locators[key]:
                    found = self._by_rule(document, part, locators, key, expected, currency, found)
                if isinstance(found, str):
                    missed(found)
                    continue
                located, read = found
                missed(f"read by learned rule {ANCHOR}" if read[0].locator.get("rule") else REPLAYED)
                trace_replay(read, locators, history, accession)
                items += read
                done.add(key)
                kept_located = kept_located or located
            if kpi_by_kpi(group) and not unlooked and done & listed and listed - done <= not_reported:
                done.add(group.key)  # every KPI the AI would look for was read by code
        # A reading of the whole document as the year outweighs a quarter's reading of part of it: a year's report
        # states its sentences once, for the year (TSMC's), and its columns, headed as years, were checked against last year.
        if left_for_ai(spec, done) is not None and (year := self._replay_year(document, spec, expected, currency)):
            return year
        return items, kept_located, done

    def _earlier_values(self, before: date) -> dict[str, list[float]]:
        """Each KPI's figures of the 13 months before a period: the periods a release's columns can show (a quarter's
        trend table runs five quarters back), whose figure a reading may have taken in place of this one's. A figure
        equal to one of years before is no sign of that (Goldman's $17 billion of net inflows in 2025 and in 2016)."""
        earlier: dict[str, list[float]] = {}
        chained = getattr(self._local, "chained", False)
        oldest = (before - timedelta(days=400)).isoformat()
        for value in list(self.values.values()):
            if chained and not _same_chain(value["period_end"], before):
                continue  # the other chains' quarters are compared after reading, in date order
            if value["method"] == "ai" and oldest <= value["period_end"] < (before - timedelta(days=20)).isoformat():
                key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
                earlier.setdefault(key, []).append(float(value["value"]))
        return earlier

    @staticmethod
    def _repeats(item: ReadValue, document: Document | None, earlier: list[float]) -> bool:
        """Whether a figure is the same number as one of its KPI's recent figures (_earlier_values), a sign another
        period's column was read in place of this one's; unless its row shows that number again in another column
        headed for the same length of period, so the filing states the two periods alike (JPMorgan's lending fees,
        1,828 in both 2Q24 and 2Q23)."""
        if item.kpi.unit not in ("currency", "count") or not any(_same(item.value, value) for value in earlier):
            return False
        return document is None or not any(_same(cell, item.value) for cell in _year_ago_cells(document, item.locator, item.value))

    def _year_history(self, expected: date) -> dict[str, dict]:
        """Last year's readings of the year (an annual report's figures, read a year before this one ends): each KPI's
        latest verified AI figure for the fiscal year ending within 20 days of a year before."""
        target = expected - timedelta(days=365)
        history: dict[str, dict] = {}
        for value in list(self.values.values()):
            if value["method"] == "ai" and value["validation_status"] == "verified" and value["fiscal_period"] == "FY" \
                    and value.get("locator") and not placed_elsewhere(value) and abs(_days(value["period_end"], target.isoformat())) <= 20:
                history[f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"] = value
        return history

    def _replay_year(self, document: Document, spec: Spec, expected: date, currency: str | None):
        """A year's report read again from last year's: TSMC files its annual consolidated report each Q4, a new file name
        every year, its figures in "Years ended" columns; replaying Q3's "three months ended" places into it is rightly
        refused. Read from last year's readings of the year, as a reading of
        the year (its figures stored for the fiscal year, Q4 derived from them as for the AI's). It stands only whole:
        every group read, or of a metric group every KPI last year's reading did not note as unreported; otherwise the AI
        reads the document as before, so a year's figures never sit beside a quarter's reading. Tried when the quarter's
        replay leaves something for the AI."""
        history = self._year_history(expected)
        last = {key: value["locator"] for key, value in history.items()}
        if not last:
            return None
        accession = next((value.get("source_accession") for value in history.values()), None)
        not_reported = self._not_reported_before(accession, expected)
        items: list[ReadValue] = []
        kept, done = None, set()
        for group in spec.groups:
            mine = {key: locator for key, locator in last.items() if key.split(".", 1)[0] == group.key}
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis if kpi.key != group.total_kpi}
            if not mine:
                if listed <= not_reported:
                    done.add(group.key)
                    continue
                return None
            if kpi_by_kpi(group):
                parts = [({key: locator}, Spec.model_validate({"groups": [{**group.model_dump(), "kpis": [
                    kpi.model_dump() for kpi in group.kpis if f"{group.key}.{kpi.key}" == key]}]}), key)
                    for key, locator in mine.items() if key in listed]
                if listed - mine.keys() - not_reported:
                    return None
            elif listed - mine.keys() - not_reported and not any(locator.get("whole_table") for locator in mine.values()):
                return None
            else:
                parts = [(mine, Spec.model_validate({"groups": [group.model_dump()]}), group.key)]
            for locators, part, key in parts:
                found = self._replay_part(document, part, locators, expected, currency, annual=True)
                if isinstance(found, str):
                    return None
                located, read = found
                trace_replay(read, locators, history, accession)
                items += read
                done.add(key)
                kept = kept or located
            done.add(group.key)
        # The document is a year's report only if some of its figures sit in columns headed as years.
        if kept is None or left_for_ai(spec, done) is not None \
                or not any(_annual_header(str(item.locator.get("header") or "")) for item in items):
            return None
        kept.annual = True
        self.result.replay_outcomes["replayed as the year"] = self.result.replay_outcomes.get("replayed as the year", 0) + 1
        return items, kept, done

    def _replay_part(self, document: Document, part: Spec, locators: dict[str, dict], expected: date,
                     currency: str | None, annual: bool = False) -> tuple[Located, list[ReadValue]] | str:
        """One group, or one KPI, replayed and checked: its reading and values, or why it does not stand. A year's
        reading (annual) names no quarter its columns must name."""
        _year, period = fiscal_label(expected, self.profile.fiscal_year_end, year_offset=getattr(self, "offset", 0))
        located = replay(document, locators, expected, int(period[1]) if period.startswith("Q") and not annual else None)
        if isinstance(located, AmbiguousQuote) and len(locators) == 1:
            return self._one_of(document, part, next(iter(locators)), located, expected, currency, annual)
        if isinstance(located, str):
            return located
        # A breakdown read part by part takes the total the filing states for its parts (totals are never stored).
        group = part.groups[0]
        if group.kind == "revenue_breakdown" and group.total_kpi and not located.tables \
                and not any(value.kpi == f"{group.key}.{group.total_kpi}" for value in located.values) \
                and (total := stated_total(document, located.values, f"{group.key}.{group.total_kpi}")):
            located.values.append(total)
        return self._checked(document, part, located, expected, currency, annual=annual)

    def _stored_year_ago(self, expected: date) -> dict[str, float]:
        """Each KPI's verified figure for the same quarter one year earlier ("group.kpi" → value)."""
        target = (expected - timedelta(days=365)).isoformat()
        return {f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}": float(v["value"])
                for v in self.values.values() if v["validation_status"] == "verified" and v["method"] in ("ai", "derived")
                and v["fiscal_period"] != "FY" and abs(_days(v["period_end"], target)) <= 12}

    @staticmethod
    def _year_ago_contradicts(item: ReadValue, document: Document, before: float | None) -> bool:
        """A table row whose own year-ago cells all differ from the figure stored for that quarter last year, by more
        than the row's printed rounding: another measure's row under the same label, not the KPI's place (Uber's
        "Freight" under Revenue, 1,337, taken for Freight Gross Bookings: Q4's release, with a full-year table more, had
        the bookings table one place later). Asked only of a place taken by last quarter's table number alone, among
        tables that give its label other figures (readers/replay.py, chosen_by_number): a label one table gives a
        figure, or every table the same one, is the KPI's even when restated since (Goldman's FICC financing, recast in
        2026). A row with no year-ago cell, or no stored figure, says nothing."""
        table = document.tables.get(str(item.locator.get("table")))
        place = (str(item.locator.get("table")), item.locator.get("row"), item.locator.get("col"))
        cells = _year_ago_cells(document, item.locator, item.value) \
            if before is not None and table is not None and place in chosen_by_number(document) else []
        if not cells:
            return False
        text = table.cell(int(item.locator["row"]), int(item.locator["col"])) or ""
        decimals = re.search(r"\.(\d+)", text)
        step = 10 ** -(len(decimals.group(1)) if decimals else 0) * abs(item.value / parse_number(text).value)
        return all(abs(cell - before) > max(step / 2, abs(before) * 1e-4) for cell in cells)

    def _one_of(self, document: Document, part: Spec, key: str, ambiguous: AmbiguousQuote, expected: date,
                currency: str | None, annual: bool = False) -> tuple[Located, list[ReadValue]] | str:
        """A KPI's sentence found in several places (KKR's "Management Fees" row in the firm's table and again in each
        segment's; Mastercard's bullets for the quarter and again for the year to date): one place is kept only on the
        filing's own evidence, after every check a Flash-only reading must pass. First the places whose row's year-ago
        column holds the figure stored for that quarter last year; failing those, the places whose section names the
        quarter rather than a longer span, for sentences of prose only. They must all give one figure (a table the filing repeats in its appendix)."""
        period_end = reported_period(document, expected).isoformat()
        passing = []
        for locator, section in ambiguous.candidates:
            checked = self._checked(document, part, Located(period_end=period_end, values=[locator]), expected, currency,
                                    annual=annual)
            if not isinstance(checked, str):
                passing.append((checked, section))
        before = self._stored_year_ago(expected).get(key)
        pool = [checked for checked, _section in passing if self._year_ago_cell_matches(checked[1][0], document, before)]
        # Sections decide only between sentences of prose that each sit under a named period, the quarter's and longer
        # spans' (Mastercard): table rows quoted as lines repeat across different tables (Apollo's "Ending Balance" ends
        # its AUM and its fee-generating AUM roll-forwards, each for the quarter and the twelve months).
        prose = not any(row_line(locator.quote or "", locator.value_text or "") for locator, _section in ambiguous.candidates)
        if not pool and prose and all(section in ("quarter", "longer") for _locator, section in ambiguous.candidates):
            pool = [checked for checked, section in passing if section == "quarter"]
        # Every place the filing gives states one figure (KKR's table and its appendix copy): the figure is the filing's
        # wherever it is read.
        if not pool and all(locator.value_text for locator, _section in ambiguous.candidates) \
                and len({parse_number(locator.value_text or "").value if parse_number(locator.value_text or "") else None
                         for locator, _section in ambiguous.candidates}) == 1:
            pool = [checked for checked, _section in passing]
        # A table row in several tables with no year-ago figure stored to decide by: last quarter's table, as before.
        if not pool and before is None:
            pool = [checked for checked, section in passing if section == "last"]
        if pool and len({round(checked[1][0].value, 9) for checked in pool}) == 1:
            return pool[0]
        return str(ambiguous)

    def _checked(self, document: Document, part: Spec, located: Located, expected: date, currency: str | None,
                 mark: dict | None = None, annual: bool = False) -> tuple[Located, list[ReadValue]] | str:
        """A reading code made (a replay, or a learned rule's), kept only when it passes every check a Flash-only
        reading must. `mark` names the rule that read it, on each value's locator. A year's reading (annual) is
        checked as one: its columns headed as years, against last year's (_confirmed_years)."""
        keys = {group.key for group in part.groups}
        located.annual = located.annual or annual
        read, problems = read_values(document, part, located, currency)
        read = [item for item in read if item.group.key in keys]
        for item in read:
            item.locator.update(mark or {})
        read, problems = self._drop_wrong_columns(read, problems, document, located, expected)
        if problems or not read:
            return "a check failed"
        if annual:
            if not keys <= self._confirmed_years(located, read, part, expected, document):
                return "not confirmed where it was or by the year-ago column"
            return located, read
        if self._far_above_quarters(read, expected):
            return "total far above the quarter before"
        if not keys <= self._confirmed_groups(located, read, part, expected, document):
            return "not confirmed where it was or by the year-ago column"
        return located, read

    @staticmethod
    def _year_column(item: ReadValue, last: dict[str, dict]) -> bool:
        """A year's reading's column: headed as a year, or headed with no period at all exactly as last year's place was
        (TSMC's wafer shipments, "Shipments (piece)"); never one headed as a quarter. _replay_year asks another group
        of the document to sit in columns headed as years."""
        header = str(item.locator.get("header") or "")
        if _annual_header(header):
            return True
        place = last.get(f"{item.group.key}.{item.kpi.key}") or {}
        return bool(header) and not _ANNUAL_HEADER.search(header) and not _QUARTER_HEADER.search(header) \
            and comparable_header(header) == comparable_header(str(place.get("header") or ""))

    def _confirmed_years(self, located: Located, read: list[ReadValue], part: Spec, expected: date,
                         document: Document) -> set[str]:
        """Groups a year's reading settles on its own, as _confirmed_groups does a quarter's, against years: its period
        holds, every column is headed as a year, nothing last year's reading found is missing, no figure repeats an
        earlier year's, every check passes against last year, and each figure is where it was last year or matches the
        figure stored for last year in its own row's year-ago column (a new row of a table that adds up to its total
        stands beside the others)."""
        if check_period(located, expected):
            return set()
        history = self._year_history(expected)
        last = {key: value["locator"] for key, value in history.items()}
        before = (expected - timedelta(days=20)).isoformat()
        earlier: dict[str, list[float]] = {}
        for value in list(self.values.values()):
            if value["method"] in ("ai", "derived") and value["fiscal_period"] == "FY" and value["validation_status"] != "rejected" \
                    and value["period_end"] < before:
                earlier.setdefault(f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}", []).append(float(value["value"]))
        stored = {key: float(value["value"]) for key, value in history.items()}
        previous = self._previous(expected, annual=True)
        confirmed = set()
        for group in part.groups:
            items = [item for item in read if item.group.key == group.key]
            parts = [item for item in items if not (item.is_total or item.kpi.key == group.total_kpi)]
            if not parts or not all(self._year_column(item, last) for item in items if item.locator.get("table")):
                continue
            keys = {f"{item.group.key}.{item.kpi.key}" for item in items}
            whole = any(item.locator.get("whole_table") for item in items)
            if missing_since(last, keys, group.key, whole, {f"{group.key}.{kpi.key}" for kpi in group.kpis}, document):
                continue
            if any(item.kpi.unit in ("currency", "count") and any(_same(item.value, value) for value in
                   earlier.get(f"{group.key}.{item.kpi.key}", [])) for item in parts):
                continue
            trial = copy.deepcopy(items)
            validate_groups(trial, previous)
            if not all(item.status == "verified" for item in trial):
                continue
            # A row new this year (TSMC's 3-nanometer, 2023) has no place last year and no year-ago figure: in a whole
            # table whose rows add up to the total it states (checked above), it stands beside rows that are confirmed.
            totalled = whole and any(item.is_total for item in items)
            if all(_same_place(item.locator, last.get(f"{group.key}.{item.kpi.key}") or {})
                   or self._year_ago_cell_matches(item, document, stored.get(f"{group.key}.{item.kpi.key}"))
                   or (totalled and f"{group.key}.{item.kpi.key}" not in last) for item in parts) \
                    and any(f"{group.key}.{item.kpi.key}" in last for item in parts):
                confirmed.add(group.key)
        return confirmed

    def _by_rule(self, document: Document, part: Spec, locators: dict[str, dict], key: str, expected: date,
                 currency: str | None, miss: str) -> tuple[Located, list[ReadValue]] | str:
        """A quoted metric KPI replay could not find, tried by the anchor-phrase rule. Live, its reading stands (but in
        one filing in ten the AI reads too, as a spot check); in shadow, or spot-checked, it waits beside the AI's
        reading and the two are compared (_settle_rule_evidence). Otherwise the replay's miss stands."""
        status = (getattr(self, "learned_rules", None) or {}).get(ANCHOR)
        if status not in (SHADOW, LIVE):
            return miss
        found = anchor_locator(document, key, locators[key])
        if isinstance(found, str):
            return miss
        locator, anchor = found
        located = Located(period_end=reported_period(document, expected).isoformat(), values=[locator])
        checked = self._checked(document, part, located, expected, currency, {"rule": ANCHOR, "anchor": anchor})
        if isinstance(checked, str):
            return miss
        accession = getattr(self._local, "accession", None)
        if status == LIVE and accession and not spot_checked(accession):
            return checked
        self._local.__dict__.setdefault("rule_candidates", {})[key] = ("spot-check" if status == LIVE else SHADOW, checked[1][0])
        return miss

    def _settle_rule_evidence(self, accession: str, read: list[ReadValue]) -> None:
        """Each learned rule's figure beside the AI's accepted reading of the same KPI in this filing: the evidence the
        service promotes or demotes the rule by. A KPI the AI did not read gives no evidence either way."""
        candidates = getattr(self._local, "rule_candidates", None) or {}
        self._local.rule_candidates = {}
        accepted = {f"{item.group.key}.{item.kpi.key}": item for item in read
                    if not item.locator.get("rule") and item.status != "rejected"}
        for key, (mode, item) in candidates.items():
            if key in accepted:
                row = evidence(ANCHOR, mode, accession, key, item, accepted[key])
                self.result.rule_evidence.append(row)
                outcome = f"{ANCHOR} {mode} {'agreed' if row['agreed'] else 'disagreed'}"
                self.result.replay_outcomes[outcome] = self.result.replay_outcomes.get(outcome, 0) + 1

    def _kind_history(self, before: date, source: str | None, quarters: bool = False) -> dict[str, dict]:
        """The AI's latest readings in the same kind of document. A kind is known by its file name, which issuers change
        (TSMC's report was d…d6k, tsm-ex991_…, tsmc…consolidatdfinan, a0515, a…consolidatedreport- in turn, so replay
        found no earlier reading for most quarters): with none under the name, the latest reading of any kind serves, and
        replay still holds only where the same rows, column and total are found and every check passes."""
        return self._ai_history(before, source, quarters) or (self._ai_history(before, None, quarters) if source is not None else {})

    def _not_reported_before(self, accession: str | None, expected: date) -> set[str]:
        """KPIs the nearest reading's AI looked for and did not find: not looked for again this quarter. In the newest
        release a KPI never read anywhere is always looked for: the audit listed it from that release, so another quarter
        not reporting it says nothing of this one (Amazon Now's cities, listed from the Q2 2026 release, were taken as
        absent there because Q1 lacked them). Older quarters keep the every-other-quarter rule."""
        notes = (self.filings.get(accession or "") or {}).get("notes") or []
        reported = next((note for note in notes if note.startswith(_NOT_REPORTED)), _NOT_REPORTED)
        not_reported = set(reported.removeprefix(_NOT_REPORTED).split(", ")) if _READ_BY_AI in notes else set()
        if expected == getattr(self, "_newest_release", None):
            ever = {f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}" for value in self.values.values()
                    if value["method"] == "ai" and value["validation_status"] != "rejected"}
            not_reported = {key for key in not_reported if key in ever}
        return not_reported
