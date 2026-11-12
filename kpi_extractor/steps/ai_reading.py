"""One earnings document read by AI: Flash, the reasoning reader, replay, and the checks a reading must pass."""
from __future__ import annotations

import copy
import re
from datetime import date, timedelta

from .. import ai
from ..ai import AiResponseError, Located, Spec
from ..ops.control import ControlError
from ..documents.document import Document, clean
from ..quality.checks import check_period, far_above_quarter, looks_like_part_of_table, total_label, validate_groups
from ..readers.extract import (ReadValue, describe, explanation_supports, header_contradicts, locator_hint, previous_from_jump,
                       read_values)
from ..readers.replay import _same_place, _source_template, asked_of, missing_since
from ..readers.trace import placed_elsewhere, stamp_call
from ..rules.period import states_quarter
from .replay_reading import in_full_groups, left_for_ai
from .shared import (AI_GROUP_PREFIX, _READ_BY_AI, _days, _document_text, _identity_text, _is_jump,
                     _later_place, _same, _same_chain, _year_ago_cells)

# About 30k tokens: enough for any earnings release, and for the revenue sections of a full financial report.
MAX_DOCUMENT_CHARS = 120_000
# A 6-K read from its own document is classified from the report after its cover; one judged on the cover before is
# asked again once (TSMC's 2016–2019 results and financial statements were all judged on the cover).
_OWN_6K_DOCUMENT = re.compile(r"6-?k", re.I)
_INVALID_POINTER = re.compile(r"unknown (table|text block)|quote not found|not inside the quote|no number in|combined line|row mismatch")

def _explain_pointer(problem: str, document: Document) -> str:
    """Retry feedback that names the fix: a text block cited as a table (ASML's statements are slide images whose
    figures come through as text) must be quoted instead."""
    match = re.search(r"unknown table (\S+)", problem)
    if match and match.group(1) in document.blocks:
        block = match.group(1)
        return (f"{problem}: {block} is a text block, not a table. Cite it as "
                f'{{"kpi": ..., "block": "{block}", "quote": "<the label and the numbers, copied exactly>", '
                f'"value_text": "<the one number for the reported period>"}}; the column headers at the start of the '
                "block give the order of the periods.")
    if match:
        return f"{problem}: the document's tables are {', '.join(list(document.tables)[:40]) or 'none'}"
    return problem



def _previous_of(notes: list[str], value: float) -> float | None:
    return next((previous for note in notes if _is_jump(note) and (previous := previous_from_jump(note, value)) is not None), None)


def _previous_text(item) -> str:
    previous = _previous_of(item.notes, item.value)
    return f", previous {previous:.4g}" if previous is not None else ""


class AiReading:
    """One earnings document read by AI: Flash, the reasoning reader, replay, and the checks a reading must pass."""

    def _read(self, text, document: Document, spec: Spec, expected: date, version: int | None = None):
        """Code first: each group read the way it was last quarter (or in the nearest quarter read), standing only on
        the checks a Flash reading must pass; this holds across KPI-list versions, so a list change sends only new or
        changed groups to the AI. The rest goes to Flash. A group Flash's reading confirms stands alone; the reasoning reader reads the
        others independently, values stand where the two agree or where the checks settle them, and the reasoning reader reviews the
        rest."""
        # A company that tags no segments has no reporting currency yet: its release's own "$" amounts say it.
        currency = self._default_currency() or document.declared_currency() or document.marked_currency()
        self._local.replayed = False
        self._local.second_read = None
        replayed, replay_located, done = self._replay_groups(document, spec, expected, currency)
        open_spec = left_for_ai(spec, done) if done else spec
        if open_spec is None:
            self._local.replayed = True
            self.result.replayed_reads += 1
            return in_full_groups(replayed, spec), [], replay_located
        read, problems, located = self._read_ai(text, document, open_spec, expected, version, currency, partial=bool(done))
        return in_full_groups(replayed + read, spec), problems, located

    def _read_ai(self, text, document: Document, spec: Spec, expected: date, version: int | None, currency: str | None,
                 partial: bool = False):
        def build(subset: Spec | None = None, hints: bool = True) -> str:
            if not callable(text):
                return text
            try:
                return text(subset, hints=hints)
            except TypeError:  # a prompt without the hints switch, or without a part of the list
                try:
                    return text(subset) if subset is not None else text()
                except TypeError:
                    return text()
        full_text = build(spec if partial else None)
        flash, flash_read, flash_problems = self._locate(full_text, document, spec, currency, model="flash", thinking_first=False)
        flash_read, flash_problems = self._drop_wrong_columns(flash_read, flash_problems, document, flash, expected)
        self._local.second_read = None
        why = self._why_not_alone(flash, flash_read, spec, expected, document)
        if why is None or (
                not flash.tables and not flash.values and self._kind_reports_nothing(document, expected, version)):
            self.result.flash_only_reads += 1
            return flash_read, [f"flash {problem}" for problem in flash_problems], flash
        # Groups Flash's reading confirms on their own stand; the reasoning reader reads only the rest of the list.
        confirmed = self._confirmed_groups(flash, flash_read, spec, expected, document)
        open_groups = [group for group in spec.groups if group.key not in confirmed]
        kept = [item for item in flash_read if item.group.key in confirmed]
        if not open_groups:
            self.result.flash_only_reads += 1
            return flash_read, [f"flash {problem}" for problem in flash_problems], flash
        subset = spec if not confirmed else Spec.model_validate({"groups": [g.model_dump() for g in open_groups],
                                                                 "names": {}})
        self.result.second_reads += 1
        self._local.second_read = why
        self.result.second_read_reasons[why] = self.result.second_read_reasons.get(why, 0) + 1
        # The second reader: Flash, reasoning first, reading the document on its own, without the hints of where each KPI
        # was found before: given them, it could repeat the first reading's mistake instead of catching it. The hints sit
        # after the document, so the prompt cache the two readings share is unchanged.
        second_text = build((spec if partial else None) if subset is spec else subset, hints=False)
        pro, pro_read, pro_problems = self._locate(second_text, document, subset, currency, model="flash", thinking_first=True)
        pro_read, pro_problems = self._drop_wrong_columns(pro_read, pro_problems, document, pro, expected)
        open_keys = {group.key for group in open_groups}
        by_flash = {f"{item.group.key}.{item.kpi.key}": item for item in flash_read if item.group.key in open_keys}
        by_pro = {f"{item.group.key}.{item.kpi.key}": item for item in pro_read}
        agreed: list[ReadValue] = list(kept)
        disputed: dict[str, tuple[ReadValue | None, ReadValue | None]] = {}
        for key in [*by_flash, *[key for key in by_pro if key not in by_flash]]:
            a, b = by_flash.get(key), by_pro.get(key)
            if a and b and _same(a.value, b.value):
                agreed.append(a)
            else:
                disputed[key] = (a, b)
        problems = [f"flash {problem}" for problem in flash_problems] + [f"pro {problem}" for problem in pro_problems]
        self._settle_by_checks(agreed, disputed, expected)
        if disputed:
            lines = [f"- {key}: A = {describe(document, a) if a else 'not reported'}; "
                     f"B = {describe(document, b) if b else 'not reported'}" for key, (a, b) in disputed.items()]
            # The reasoning reader reviews only the tables and passages the two readings point to, with the opening for the period.
            pointed = {str(item.locator.get("table") or item.locator.get("block")) for pair in disputed.values()
                       for item in pair if item}
            review = self._ask(ai.prompt(self.company, document.render_ids(pointed, MAX_DOCUMENT_CHARS, opening=3),
                                         ai.REVIEW_TASK, "Disputed KPIs:\n" + "\n".join(lines)),
                               "review", ai.Review, thinking_first=True, model="flash")
            for key, (a, b) in disputed.items():
                chosen = {"A": a, "B": b}.get(review.choices.get(key, "none"))
                reason = review.reasons.get(key, "unstated")
                if chosen is None:
                    problems.append(f"{key}: readings disagree and review kept neither ({reason})")
                    continue
                chosen.notes.append("confirmed by review")
                chosen.locator["review_reason"] = reason
                if call := self._call_of(review):
                    chosen.locator["review_call"] = call
                agreed.append(chosen)
        located = flash if flash.period_end else pro
        # Why each KPI was not found: the reading that stands, then the other reader's for KPIs it gives none.
        located.missing_reasons = {**(pro if located is flash else flash).missing_reasons, **located.missing_reasons}
        return agreed, problems, located

    def _confirmed_groups(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                          document: Document) -> set[str]:
        """Groups a single reading settles on its own: the reading's period holds, the group passes every check, no value
        repeats an earlier period's number, nothing found last quarter is missing, and each value is where it was last
        quarter or matches the year-ago figure in its own row. None when the reading's period itself is in doubt."""
        if check_period(located, expected) or located.annual or self._reports_full_year(read, expected):
            return set()
        earlier = self._earlier_values(expected)
        last = self._last_locators(expected, _source_template(document.source_url))
        stored = self._stored_year_ago(expected)
        confirmed = set()
        for group in spec.groups:
            items = [item for item in read if item.group.key == group.key]
            parts = [item for item in items if not (item.is_total or item.kpi.key == group.total_kpi)]
            if not parts:
                continue
            keys = {f"{item.group.key}.{item.kpi.key}" for item in items}
            whole = any(item.locator.get("whole_table") for item in items)
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis}
            # Something this group had in the reference quarter is missing now (see missing_since for what is not
            # missing: a key the current list no longer has, a row the filing no longer has).
            if missing_since(last, keys, group.key, whole, listed, document):
                continue
            if any(self._repeats(item, document, earlier.get(f"{group.key}.{item.kpi.key}", [])) for item in parts):
                continue
            trial = copy.deepcopy(items)
            validate_groups(trial, self._previous(expected))
            if not all(item.status == "verified" for item in trial):
                continue
            if all(self._same_place_as_last(item, expected, document)
                   or self._year_ago_cell_matches(item, document, stored.get(f"{group.key}.{item.kpi.key}")) for item in parts):
                confirmed.add(group.key)
        return confirmed

    def _settle_by_checks(self, agreed: list[ReadValue], disputed: dict[str, tuple], expected: date) -> None:
        """Two readings of a group that disagree, settled without a reasoning review only on structural evidence: one adds up
        (a breakdown to its total, a mix to 100%) and holds no number repeated from an earlier period, while the other
        does not add up or repeats an earlier period's number. A jump alone never decides: last quarter's figure read
        from another column passes the jump check precisely because it did not move, so a jump goes to the review and
        the explanation step as before. A single metric one reader found and the other did not goes to the review."""
        earlier = self._earlier_values(expected)
        for group in {key.split(".", 1)[0] for key in disputed}:
            pairs = {key: pair for key, pair in disputed.items() if key.split(".", 1)[0] == group}
            sample = next(item for pair in pairs.values() for item in pair if item)
            if sample.group.kind == "metric":
                continue  # nothing to add up against
            base = [item for item in agreed if item.group.key == group]
            sound, broken = [], []
            for side in (0, 1):
                rows = base + [pair[side] for pair in pairs.values() if pair[side]]
                trial = copy.deepcopy(rows)
                validate_groups(trial, {})  # sums and totals only: no previous quarter, so no jump can decide
                adds_up = bool(rows) and all(item.status == "verified" for item in trial)
                repeats = any(item.kpi.unit in ("currency", "count") and any(
                    _same(item.value, value) for value in earlier.get(f"{group}.{item.kpi.key}", []))
                    for pair in pairs.values() if (item := pair[side]))
                sound.append(adds_up and not repeats)
                broken.append(not adds_up or repeats)
            if sound[0] and broken[1] or sound[1] and broken[0]:
                winner = 0 if sound[0] else 1
                for key, pair in pairs.items():
                    if pair[winner]:
                        pair[winner].notes.append("settled by the checks")
                        agreed.append(pair[winner])
                    del disputed[key]

    def _locate(self, text: str, document: Document, spec: Spec, currency: str | None, model: str, thinking_first: bool):
        """One reader's answer read back by code, with one retry when its pointers do not hold (a text block cited as a
        table, a table id the document does not have), the reader told which and why."""
        located = self._ask(text, "locate", Located, thinking_first=thinking_first, model=model)
        read, problems = read_values(document, spec, located, currency)
        invalid = [problem for problem in problems if _INVALID_POINTER.search(problem)]
        if invalid:
            retry_text = text + "\n\nYour previous answer had pointers that do not hold:\n" + "\n".join(
                f"- {_explain_pointer(problem, document)}" for problem in invalid[:20])
            located = self._ask(retry_text, "locate", Located, thinking_first=thinking_first, model=model)
            read, problems = read_values(document, spec, located, currency)
        stamp_call(read, self._call_of(located))
        return located, read, problems

    def _kind_reports_nothing(self, document: Document, expected: date, version: int | None) -> bool:
        """Whether both models already read this kind of document for this KPI list and found nothing in it (TSMC's
        earnings release once its amounts come from the quarterly report). Flash pointing at nothing again then stands;
        a pointer that does not hold (a partial table) still goes to the reasoning reader."""
        if version is None:
            return False
        if _OWN_6K_DOCUMENT.search(document.source_url.rsplit("/", 1)[-1]):
            # A 6-K's own document is named for the form, not its content (TSMC's "d582139d6k.htm" is a results release
            # one quarter and the financial statements the next): its name says nothing of what an earlier one held.
            return False
        source = _source_template(document.source_url)
        return any(filing.get("spec_version") == version and filing["status"] == "skipped"
                   and _READ_BY_AI in (filing.get("notes") or []) and filing.get("value_count", 0) == 0
                   and _source_template(filing.get("source_url") or "") == source
                   and (filing.get("period_end") or "9999") < expected.isoformat()
                   for filing in self.filings.values())

    def _flash_is_enough(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                         document: Document) -> bool:
        return self._why_not_alone(located, read, spec, expected, document) is None

    def _why_not_alone(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                       document: Document) -> str | None:
        """Why one reading does not stand without a second (None when it does): its period must check, no value may
        repeat an earlier period's number, every check must pass, and either every KPI sits where it sat last quarter
        or the year-ago column confirms it. The reason is logged with the filing and counted in the run summary."""
        if check_period(located, expected):
            return "period"
        if located.annual or self._reports_full_year(read, expected):
            return "annual"
        earlier = self._earlier_values(expected)
        for item in read:
            key = f"{item.group.key}.{item.kpi.key}"
            if item.is_total or item.kpi.key == item.group.total_kpi:
                continue  # totals are not stored; the parts must still add up to them
            if self._repeats(item, document, earlier.get(key, [])):
                return "repeats_earlier"  # the same number as an earlier period: likely a prior-period column
        trial = copy.deepcopy(read)
        validate_groups(trial, self._previous(expected))
        if not all(item.status == "verified" for item in trial):
            return "checks_failed"
        if self._where_it_was(read, expected, document, spec) or self._year_ago_confirms(read, document, expected):
            return None
        last = asked_of(self._last_locators(expected, _source_template(document.source_url)), spec)
        if not last:
            return "not_placed"  # a company's first quarters, or a new kind of document
        keys = {f"{item.group.key}.{item.kpi.key}" for item in read}
        return "missing_now" if any(key not in keys for key in last) else "moved"

    def _same_place_as_last(self, item: ReadValue, expected: date, document: Document) -> bool:
        last = self._last_locators(expected, _source_template(document.source_url))
        key = f"{item.group.key}.{item.kpi.key}"
        return key in last and _same_place(item.locator, last[key])

    def _where_it_was(self, read: list[ReadValue], expected: date, document: Document, spec: Spec) -> bool:
        last = asked_of(self._last_locators(expected, _source_template(document.source_url)), spec)
        if not last:
            return False  # a company's first quarters, and a new kind of document, are not placed yet
        keys = {f"{item.group.key}.{item.kpi.key}" for item in read}
        if any(key not in keys for key in last):
            return False  # something found last quarter is missing now
        return all(item.is_total or item.kpi.key == item.group.total_kpi
                   or (f"{item.group.key}.{item.kpi.key}" in last
                       and _same_place(item.locator, last[f"{item.group.key}.{item.kpi.key}"]))
                   for item in read)

    def _year_ago_confirms(self, read: list[ReadValue], document: Document, expected: date) -> bool:
        """Every value is accounted for: confirmed by the release's own year-ago column (read by code from the same row,
        in a column headed for the same length of period, equal to what is stored for that quarter last year) or found
        where it was last quarter; at least two by the year-ago column. A value with neither (a new KPI, a quoted
        figure) sends the reading to the reasoning reader. Costs no AI."""
        stored = self._stored_year_ago(expected)
        confirmed = 0
        for item in read:
            if item.is_total or item.kpi.key == item.group.total_kpi:
                continue
            if self._year_ago_cell_matches(item, document, stored.get(f"{item.group.key}.{item.kpi.key}")):
                confirmed += 1
            elif not self._same_place_as_last(item, expected, document):
                return False
        return confirmed >= 2

    @staticmethod
    def _year_ago_cell_matches(item: ReadValue, document: Document, before: float | None) -> bool:
        return before is not None and any(abs(cell - before) <= abs(before) * 1e-4
                                          for cell in _year_ago_cells(document, item.locator, item.value))

    def _flag_partial_tables(self, read: list[ReadValue], expected: date, document: Document) -> None:
        """A breakdown read with fewer rows than in the reference quarter and a total under half of that quarter's is
        most likely part of its table, with a subtotal taken for the total (TSMC's 2nm and 3nm under "advanced
        technologies"): its parts add up, so only the comparison shows it. Flagged whole."""
        history = self._kind_history(expected, _source_template(document.source_url))
        for group in {item.group.key for item in read if item.group.kind == "revenue_breakdown"}:
            items = [item for item in read if item.group.key == group]
            total = next((item.value for item in items if item.is_total or item.kpi.key == item.group.total_kpi), None)
            parts = [item for item in items if not (item.is_total or item.kpi.key == item.group.total_kpi)]
            reference = [value for key, value in history.items() if key.split(".", 1)[0] == group]
            if not reference or total is None:
                continue
            newest = max(value["period_end"] for value in reference)
            before = [value for value in reference if value["period_end"] == newest]
            before_total = next((float(v["locator"]["total"]) for v in before if (v.get("locator") or {}).get("total")), None)
            if looks_like_part_of_table(len(parts), total, len(before), before_total, total_label(items)):
                for item in items:
                    item.status = "needs_review"
                    item.notes.append("fewer rows and under half the total of the quarter compared: likely part of the table")

    def _explain_jumps(self, read: list[ReadValue], document: Document, spec: Spec) -> None:
        """Values flagged only for a sharp change get a reading by the reasoning reader; a real change backed by a quote stands."""
        flagged = [item for item in read if item.status == "needs_review"
                   and item.notes and all(_is_jump(note) for note in item.notes if note != "confirmed by review")]
        if not flagged:
            return
        lines = "\n".join(f"- {item.group.key}.{item.kpi.key} ({item.group.label} / {item.kpi.label}): now {item.value:g}"
                          f"{_previous_text(item)}; {'; '.join(item.notes)}" for item in flagged)
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.EXPLAIN_TASK, lines),
                               "explain", ai.Explanations, thinking_first=True, model="flash")
        except (AiResponseError, ControlError):
            return  # the flagged values stay needs_review
        haystack = _document_text(document)
        for item in flagged:
            verdict = answer.items.get(f"{item.group.key}.{item.kpi.key}")
            quote = clean(verdict.quote or "") if verdict else ""
            if verdict and verdict.legitimate and quote in haystack and explanation_supports(
                    quote, item.kpi.label, item.value, _previous_of(item.notes, item.value)):
                item.status = "verified"
                item.notes = [*item.notes, f"change explained: {quote[:200]}"]
                if call := self._call_of(answer):
                    item.locator["explain_call"] = call

    def _ask(self, text: str, purpose: str, schema, thinking_first: bool, model: str = "flash"):
        last_error = None
        for thinking in (thinking_first, True):
            answer = self._waiting(lambda: self.control.ai(self.symbol, purpose, ai.SYSTEM, text, thinking, model,
                                                           prompt_version=ai.prompt_version(purpose)))
            self.result.ai_calls += 1
            if not answer:
                raise AiResponseError("AI is unavailable")
            try:
                parsed = ai.parse(answer, schema)
            except AiResponseError as error:
                last_error = error
                continue
            if getattr(answer, "call_id", None):
                calls = self.__dict__.setdefault("_ai_calls", {})
                calls[id(parsed)] = (parsed, answer.call_id)  # the answer is kept, so its id is never reused
            return parsed
        raise last_error

    def _call_of(self, answer) -> str | None:
        """The id of the AI call an answer came from: stored with the figures it located, reviewed or explained."""
        return (self.__dict__.get("_ai_calls", {}).get(id(answer)) or (None, None))[1]

    def _prompt_document(self, document: Document, spec: Spec | None) -> str:
        """The document as the AI reads it: whole when it fits, else the sections Flash picks from its outline."""
        key = str(id(document))
        if key in self._prompt_documents:
            return self._prompt_documents[key]
        if document.fits(MAX_DOCUMENT_CHARS):
            text = document.render()
        elif spec is not None and (found := self._sections_with_every_kpi(document, spec)):
            text = document.render_ids(found, MAX_DOCUMENT_CHARS)  # code found every KPI: no selection call
        else:
            wanted = ai.kpi_lines(spec) if spec else "(no list yet: revenue breakdowns and operating metrics)"
            try:
                answer = self._ask(ai.prompt(self.company, document.outline(), ai.SELECT_TASK, f"KPIs:\n{wanted}"),
                                   "select", ai.Selection, thinking_first=False, model="flash")
                ids = {item for item in answer.ids if item in document.tables or item in document.blocks}
            except AiResponseError:
                ids = set()
            # Sections that name a listed KPI go in whatever Flash picks: its outline is cut off in a long report, and
            # TSMC's 2018 statements hold the technology table past that cut (with no 2nm–7nm rows to name them all).
            named = self._sections_naming_kpis(document, spec) if spec is not None else set()
            ids |= named
            text = document.render_ids(ids, MAX_DOCUMENT_CHARS, first=named) if ids else document.render_for_prompt(MAX_DOCUMENT_CHARS)
        self._prompt_documents[key] = text
        return text

    @staticmethod
    def _drop_wrong_columns(read: list[ReadValue], problems: list[str], document: Document, located: Located,
                            expected: date) -> tuple[list[ReadValue], list[str]]:
        """Values whose column header names another period (the year-ago column of a "2026 | 2025" table, which
        column_header's blanked dates make look identical) are dropped before any check, so a second reader or the
        review reads them again. The quarter's end is the filing's own, or the reading's when its period checks."""
        ends = [date.fromisoformat(located.period_end[:10])] if located.period_end and not check_period(located, expected) else []
        kept, problems = [], list(problems)
        for item in read:
            table = document.tables.get(str(item.locator.get("table")))
            row, col = item.locator.get("row"), item.locator.get("col")
            why = header_contradicts(table, int(row), int(col), expected, *ends) if table is not None and row is not None \
                and col is not None else None
            if why:
                problems.append(f"{item.group.key}.{item.kpi.key}: {why}")
            else:
                kept.append(item)
        return kept, problems

    def _left_out_naming(self, document: Document, spec: Spec, keys: list[str]) -> set[str]:
        """The KPIs among `keys` that a part of a long document left out of the reading names: a table row with the
        KPI's label (or last quarter's row label), or a passage containing a label of two words or more. Such a KPI may
        be reported in the cut part (the unread-figures lead); a whole document leaves nothing out."""
        text = self._prompt_documents.get(str(id(document)))
        if not keys or text is None or document.fits(MAX_DOCUMENT_CHARS):
            return set()
        kept = set(re.findall(r"^\[([^\]\s]+)\]", text, flags=re.M))
        left = [key for key in document.order if key not in kept]
        hints = {key: (value.get("locator") or {}).get("row_label") for key, value in self._ai_history(date.max).items()}
        rows = {_identity_text(row[0]) for key in left if key in document.tables for row in document.tables[key].rows if row}
        passages = [_identity_text(document.blocks[key].text) for key in left if key in document.blocks]
        found = set()
        for group in spec.groups:
            for kpi in group.kpis:
                key = f"{group.key}.{kpi.key}"
                if key not in keys:
                    continue
                labels = [label for label in (kpi.label, hints.get(key) or "") if _identity_text(label)]
                names = {_identity_text(label) for label in labels}
                phrases = {_identity_text(label) for label in labels if len(label.split()) >= 2}
                if names & rows or any(phrase in passage for phrase in phrases for passage in passages):
                    found.add(key)
        return found

    def _sections_naming_kpis(self, document: Document, spec: Spec) -> set[str]:
        """The tables with a row named for a listed KPI (by its label, or the row label it had last quarter)."""
        hints = {key: (value.get("locator") or {}).get("row_label") for key, value in self._ai_history(date.max).items()}
        names = {_identity_text(kpi.label) for group in spec.groups for kpi in group.kpis if kpi.key != group.total_kpi}
        names |= {_identity_text(label) for label in hints.values() if label}
        names.discard("")
        return {key for key, table in document.tables.items() if any(_identity_text(row[0]) in names for row in table.rows if row)}

    def _sections_with_every_kpi(self, document: Document, spec: Spec) -> set[str] | None:
        """The tables and passages naming each listed KPI (by its label, or the row label it had last quarter), when
        every KPI is found and together they fit; None sends the choice to Flash."""
        hints = {key: (value.get("locator") or {}).get("row_label") for key, value in self._ai_history(date.max).items()}
        found: set[str] = set()
        for group in spec.groups:
            for kpi in group.kpis:
                if kpi.key == group.total_kpi:
                    continue
                names = {_identity_text(kpi.label)} | ({_identity_text(hints[f"{group.key}.{kpi.key}"])}
                                                       if hints.get(f"{group.key}.{kpi.key}") else set())
                names.discard("")
                hits = {key for key, table in document.tables.items()
                        if any(_identity_text(row[0]) in names for row in table.rows if row)}
                hits |= {key for key, block in document.blocks.items()
                         if any(name and name in _identity_text(block.text) for name in names if len(name) >= 8)}
                if not hits:
                    return None
                found |= hits
        rendered = sum(len((document.tables.get(key) or document.blocks[key]).render()) for key in found)
        return found if rendered <= MAX_DOCUMENT_CHARS * 0.9 else None

    def _ai_history(self, before: date, source: str | None = None) -> dict[str, dict]:
        """The latest AI value of each KPI before a date (optionally only from one kind of document); when none is
        before it (an older quarter read after newer ones), the nearest reading after it, which plays the same part:
        where each KPI was found in the nearest quarter. A figure whose place holds another figure never serves."""
        latest: dict[str, dict] = {}
        later: dict[str, dict] = {}
        cutoff = before.isoformat()
        chained = getattr(self._local, "chained", False)
        for value in list(self.values.values()):
            if value["method"] != "ai" or placed_elsewhere(value) or abs(_days(value["period_end"], cutoff)) <= 20:
                continue
            if chained and (value["period_end"] > cutoff or not _same_chain(value["period_end"], before)):
                continue  # in a chain: only its own earlier quarters, which are always read first
            # The kind of document is its filing's: a figure links to the exhibit it was read from, which may not be the first.
            filing_url = (self.filings.get(value.get("source_accession") or "") or {}).get("source_url") or value.get("source_url") or ""
            if source is not None and _source_template(filing_url) != source:
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] < cutoff:
                if _later_place(value, latest.get(key)):
                    latest[key] = value
            elif value["period_end"] < later.get(key, {}).get("period_end", "9999"):
                later[key] = value
        return latest or later

    def _hints(self, before: date) -> dict[str, str]:
        return {key: hint for key, value in self._ai_history(before).items() if (hint := locator_hint(value.get("locator")))}

    def _last_locators(self, before: date, source: str) -> dict[str, dict]:
        """Where each KPI was found the previous quarter in the same kind of document (TSMC's earnings release and its
        financial report carry different KPIs, and are compared only with their own kind; see _kind_history)."""
        history = self._kind_history(before, source)
        if not history:
            return {}
        newest = max(value["period_end"] for value in history.values())
        return {key: value["locator"] for key, value in history.items()
                if value["period_end"] == newest and value.get("locator") and value["validation_status"] == "verified"}

    def _far_above_quarters(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown's total is several times the quarter just before it (an annual report read as a
        quarter). Only the adjacent quarter counts: a fast grower's Q4 is far above its quarters of years ago. Size is only
        a reason to look: a breakdown whose every column is headed as a quarter states its period, and stands."""
        breakdown = [item for item in read if item.group.kind == "revenue_breakdown"]
        if breakdown and all(states_quarter(str(item.locator.get("header") or "")) for item in breakdown):
            return False
        for item in read:
            if item.group.kind != "revenue_breakdown" or not (item.is_total or item.kpi.key == item.group.total_kpi):
                continue
            before = self._quarter_total_before(AI_GROUP_PREFIX + item.group.key, period_end.isoformat())
            if far_above_quarter(item.value, before):
                return True
        return False

    def _quarter_total_before(self, group_key: str, period_end: str) -> float | None:
        """The reported total of the group's latest quarter within 120 days before period_end. In a reading chain, where
        that quarter may not be read yet, the quarter's own revenue in XBRL (an annual total is about four times it)."""
        if getattr(self._local, "chained", False):
            return next((float(v["locator"]["total"]) for v in list(self.values.values())
                         if v["method"] == "xbrl" and v["fiscal_period"] != "FY" and (v.get("locator") or {}).get("total")
                         and abs(_days(v["period_end"], period_end)) <= 12), None)
        candidates = [v for v in self.values.values()
                      if v["group_key"] == group_key and v["fiscal_period"] in ("Q1", "Q2", "Q3", "Q4")
                      and v["validation_status"] != "rejected" and (v.get("locator") or {}).get("total")
                      and 0 < _days(period_end, v["period_end"]) <= 120]
        latest = max(candidates, key=lambda v: v["period_end"], default=None)
        return float(latest["locator"]["total"]) if latest else None
