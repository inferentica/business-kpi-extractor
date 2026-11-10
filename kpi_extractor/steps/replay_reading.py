"""Code reads first: each group read again, with no AI, the way the nearest quarter of the same kind of document was
read; a metric group's KPIs one by one, since nothing adds them up. A reading stands only on the checks a Flash-only
reading must pass. What code leaves goes to the AI, and each group (or metric KPI) the AI reads instead says why, in
the run summary's replayOutcomes: the learning loop's measure of where code should learn next."""
from __future__ import annotations

from datetime import date

from ..ai import GroupSpec, Located, Spec
from ..documents.document import Document
from ..readers.extract import ReadValue, read_values
from ..readers.replay import _source_template, replay
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
        history = self._ai_history(expected, source)
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
            if kpi_by_kpi(group):
                parts = [({key: locator}, Spec.model_validate({"groups": [{**group.model_dump(), "kpis": [
                    kpi.model_dump() for kpi in group.kpis if f"{group.key}.{kpi.key}" == key]}]}), key)
                    for key, locator in mine.items() if key in listed]
                missed("a listed KPI not looked for yet", len(unlooked))
            elif unlooked and not any(locator.get("whole_table") for locator in mine.values()):
                missed("a listed KPI not looked for yet")
                continue
            else:
                parts = [(mine, Spec.model_validate({"groups": [group.model_dump()]}), group.key)]
            for locators, part, key in parts:
                found = self._replay_part(document, part, locators, expected, currency)
                if isinstance(found, str):
                    missed(found)
                    continue
                located, read = found
                missed(REPLAYED)
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
        located = replay(document, locators, expected)
        if isinstance(located, str):
            return located
        keys = {group.key for group in part.groups}
        read, problems = read_values(document, part, located, currency)
        read = [item for item in read if item.group.key in keys]
        read, problems = self._drop_wrong_columns(read, problems, document, located, expected)
        if problems or not read:
            return "a check failed"
        if self._far_above_quarters(read, expected):
            return "total far above the quarter before"
        if not keys <= self._confirmed_groups(located, read, part, expected, document):
            return "not confirmed where it was or by the year-ago column"
        return located, read

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
