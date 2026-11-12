"""Code reads first: each group read again, with no AI, the way the nearest quarter of the same kind of document was
read; a metric group's KPIs one by one, since nothing adds them up. A reading stands only on the checks a Flash-only
reading must pass. What code leaves goes to the AI, and each group (or metric KPI) the AI reads instead says why, in
the run summary's replayOutcomes: the learning loop's measure of where code should learn next."""
from __future__ import annotations

from datetime import date

from ..ai import GroupSpec, Located, Spec
from ..documents.document import Document
from ..domain.fiscal import fiscal_label
from ..readers.extract import ReadValue, read_values
from ..readers.anchor import KIND as ANCHOR, anchor_locator
from ..readers.replay import AmbiguousQuote, _source_template, replay, reported_period
from ..ops.learned import LIVE, SHADOW, evidence, spot_checked
from ..readers.trace import trace_replay
from .shared import AI_GROUP_PREFIX, _NOT_REPORTED, _READ_BY_AI

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
        outcomes = self.result.replay_outcomes

        def missed(why: str, times: int = 1) -> None:
            if times:
                outcomes[why] = outcomes.get(why, 0) + times
        if not last:
            missed("no earlier reading of this kind of document", len(spec.groups))
            return [], None, set()
        history = self._kind_history(expected, source)
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
        return items, kept_located, done

    def _replay_part(self, document: Document, part: Spec, locators: dict[str, dict], expected: date,
                     currency: str | None) -> tuple[Located, list[ReadValue]] | str:
        """One group, or one KPI, replayed and checked: its reading and values, or why it does not stand."""
        _year, period = fiscal_label(expected, self.profile.fiscal_year_end, year_offset=getattr(self, "offset", 0))
        located = replay(document, locators, expected, int(period[1]) if period.startswith("Q") else None)
        if isinstance(located, AmbiguousQuote) and len(locators) == 1:
            return self._one_of(document, part, next(iter(locators)), located, expected, currency)
        if isinstance(located, str):
            return located
        return self._checked(document, part, located, expected, currency)

    def _one_of(self, document: Document, part: Spec, key: str, ambiguous: AmbiguousQuote, expected: date,
                currency: str | None) -> tuple[Located, list[ReadValue]] | str:
        """A KPI's sentence found in several places (KKR's "Management Fees" row in the firm's table and again in each
        segment's; Mastercard's bullets for the quarter and again for the year to date): one place is kept only on the
        filing's own evidence, after every check a Flash-only reading must pass. First the places whose row's year-ago
        column holds the figure stored for that quarter last year; failing those, the places whose section names the
        quarter rather than a longer span. They must all give one figure (a table the filing repeats in its appendix)."""
        period_end = reported_period(document, expected).isoformat()
        passing = []
        for locator, section in ambiguous.candidates:
            checked = self._checked(document, part, Located(period_end=period_end, values=[locator]), expected, currency)
            if not isinstance(checked, str):
                passing.append((checked, section))
        before = self._stored_year_ago(expected).get(key)
        pool = [checked for checked, _section in passing if self._year_ago_cell_matches(checked[1][0], document, before)] \
            or [checked for checked, section in passing if section == "quarter"]
        if pool and len({round(checked[1][0].value, 9) for checked in pool}) == 1:
            return pool[0]
        return str(ambiguous)

    def _checked(self, document: Document, part: Spec, located: Located, expected: date, currency: str | None,
                 mark: dict | None = None) -> tuple[Located, list[ReadValue]] | str:
        """A reading code made (a replay, or a learned rule's), kept only when it passes every check a Flash-only
        reading must. `mark` names the rule that read it, on each value's locator."""
        keys = {group.key for group in part.groups}
        read, problems = read_values(document, part, located, currency)
        read = [item for item in read if item.group.key in keys]
        for item in read:
            item.locator.update(mark or {})
        read, problems = self._drop_wrong_columns(read, problems, document, located, expected)
        if problems or not read:
            return "a check failed"
        if self._far_above_quarters(read, expected):
            return "total far above the quarter before"
        if not keys <= self._confirmed_groups(located, read, part, expected, document):
            return "not confirmed where it was or by the year-ago column"
        return located, read

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

    def _kind_history(self, before: date, source: str | None) -> dict[str, dict]:
        """The AI's latest readings in the same kind of document. A kind is known by its file name, which issuers change
        (TSMC's report was d…d6k, tsm-ex991_…, tsmc…consolidatdfinan, a0515, a…consolidatedreport- in turn, so replay
        found no earlier reading for most quarters): with none under the name, the latest reading of any kind serves, and
        replay still holds only where the same rows, column and total are found and every check passes."""
        return self._ai_history(before, source) or (self._ai_history(before) if source is not None else {})

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
