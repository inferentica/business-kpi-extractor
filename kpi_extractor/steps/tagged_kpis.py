"""KPIs filled from a report's XBRL tags, and breakdown periods settled by exact tagged figures."""
from __future__ import annotations

import re

from datetime import date

from .. import ai
from ..ai import AiResponseError, Spec
from ..control import ControlError
from ..fiscal import fiscal_label
from ..sec import FilingRef
from ..tagged_metrics import (agrees, fact_lines, learn_tag, learn_tags, quarter_facts, tag_from_locator, unit_fits,
                              year_ago_facts)
from .shared import AI_GROUP_ORDER, AI_GROUP_PREFIX, _UNANCHORED, _is_jump, _rounding, value_key

# Where the AI's tag picks are kept between runs, beside the XBRL row curations.
TAG_PICKS = "xbrl_tags"


_RATIO_CONCEPT = re.compile(r"Ratio|Percent|Rate|Margin|Yield|To[A-Z]")


def _percent_scale(found: dict) -> dict:
    """A quarter's facts as a percent KPI reads them: a ratio concept's decimal (0.1747) as a percentage (17.47)."""
    return {tag: (value * 100 if abs(value) <= 1.5 and _RATIO_CONCEPT.search(tag.concept.split(":")[-1]) else value)
            for tag, value in found.items()}


def _percent_view(facts_of):
    return lambda quarter: _percent_scale(facts_of(quarter))


class TaggedKpiReading:
    """KPIs filled from a report's XBRL tags, and breakdown periods settled by exact tagged figures."""

    def _fill_tagged_metrics(self, reports: list[FilingRef], spec: Spec) -> list[dict]:
        """Quarters of an operating KPI its releases do not state, from the reports' XBRL where the KPI is tagged
        (Microsoft's commercial remaining performance obligation, in its releases only from FY2026). The tag is learned
        from quarters both give (see tagged_metrics); a quarter the release states, or the owner decided, is left as it
        is. Reports are read newest first, and a KPI's history ends where two reports in a row do not tag it."""
        quarters: dict[tuple[str, str], tuple[FilingRef, date]] = {}
        for ref in reports:
            # The stored filing knows its period end (not its fiscal label: the control plane does not return it).
            end = (self.filings.get(ref.accession) or {}).get("period_end")
            if not end:
                continue
            end = date.fromisoformat(str(end)[:10])
            try:
                year, period = fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
            except Exception:  # noqa: BLE001 - a period off the company's calendar is left out
                continue
            quarters[(year, period)] = (ref, end)
        if not quarters:
            return []
        facts: dict[tuple[str, str], dict] = {}

        def facts_of(quarter: tuple[str, str]) -> dict:
            if quarter not in facts:
                ref, end = quarters[quarter]
                try:
                    xbrl = self._xbrl(ref)
                    facts[quarter] = quarter_facts(xbrl.facts.to_dataframe(), end) if xbrl is not None else {}
                except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                    facts[quarter] = {}
            return facts[quarter]

        newest_first = sorted(quarters, reverse=True)
        records: list[dict] = []
        unread: list[tuple] = []
        picked: dict[str, object] = {}
        for _pass in range(2):
            records += self._tagged_records(spec, quarters, newest_first, facts_of, facts, unread, picked)
            if _pass or not unread:
                break
            picked = self._pick_tags(unread, quarters, newest_first, facts_of)
            if not picked:
                break
        if records:
            self.log(f"{self.symbol}: {len(records)} KPI values read from reports' XBRL where the releases do not give them")
        return records

    def _kpi_template(self, group, kpi, key: str) -> dict:
        """The stored shape of a KPI no release has given yet."""
        return {"group_key": key, "group_label": group.label, "group_kind": group.kind, "kpi_label": kpi.label,
                "kpi_order": group.kpis.index(kpi), "group_order": AI_GROUP_ORDER + self._group_index(key), "unit": kpi.unit,
                "currency": self._default_currency() if kpi.unit == "currency" else None}

    def _group_index(self, key: str) -> int:
        return next((value["group_order"] - AI_GROUP_ORDER for value in self.values.values() if value["group_key"] == key), 0)

    def _pick_tags(self, unread: list[tuple], quarters, newest_first, facts_of) -> dict[str, object]:
        """The AI points to the tagged figure that is each KPI no release gives, in the latest report; code keeps a pick
        only when its unit fits the KPI and the same tag is in the report before (a KPI the company reports)."""
        latest = newest_first[0]
        # The answer for one report and one set of KPIs is kept between runs: the same question was asked again on
        # every run (Adobe, Amazon, Alphabet, SpaceX: an AI call each, every all-company run, for nothing new).
        cache_key = (TAG_PICKS, f"{quarters[latest][0].accession}|{','.join(sorted(kpi.key for _g, kpi, _k in unread))}"[:2000])
        if cache_key in self._curations:
            return dict(choice.split("=", 1) for choice in self._curations[cache_key] if "=" in choice)
        lines = fact_lines(self._xbrl(quarters[latest][0]).facts.to_dataframe(), quarters[latest][1])
        ids = {f"F{index}": line for index, line in enumerate(lines)}
        document = "\n".join(f"[{id_}] {label} | {value:,.15g} | {unit}" for id_, (_tag, value, label, unit) in ids.items())
        wanted = "\n".join(f"- {kpi.key}: {kpi.label} ({kpi.unit})" for _group, kpi, _key in unread)
        try:
            answer = self._ask(ai.prompt(self.company, document, ai.TAG_TASK, f"KPIs:\n{wanted}"), "tags", ai.TagPicks,
                               thinking_first=True, model="flash")
        except (AiResponseError, ControlError) as error:
            self.result.errors.append(f"KPI tags: {error}"[:300])
            return {}
        picked = {}
        earlier = next((quarter for quarter in newest_first[1:] if facts_of(quarter)), None)
        for _group, kpi, _key in unread:
            line = ids.get(answer.picks.get(kpi.key) or "")
            if line is None or not unit_fits(kpi.unit, line[3], kpi.label, line[0].concept) or earlier is None \
                    or line[0] not in facts_of(earlier):
                continue
            picked[kpi.key] = line[0]
        if picked:
            self.log(f"{self.symbol}: tags picked for {', '.join(sorted(picked))}")
        chosen = [f"{key}={tag}"[:120] for key, tag in sorted(picked.items())]
        self._curations[cache_key] = chosen
        self.store.curation(TAG_PICKS, cache_key[1], chosen)  # an empty choice is kept too: no tag fits
        return picked

    def _tagged_records(self, spec: Spec, quarters, newest_first, facts_of, opened: dict, unread: list[tuple],
                        picked: dict) -> list[dict]:
        records: list[dict] = []
        unread.clear()
        for group in spec.groups:
            # A breakdown's lines only take the report's exact figure (Adobe's customer groups, rounded to $10 million
            # in its releases, tagged to the dollar in its 10-Qs); a quarter is never filled one line at a time.
            split = group.kind == "revenue_breakdown"
            if group.kind != "metric" and not split:
                continue
            key = f"{AI_GROUP_PREFIX}{group.key}"
            for kpi in group.kpis:
                # XBRL states a ratio as a decimal (Meridian's CET1 0.1747): a percent KPI reads it as 17.47.
                kfacts = _percent_view(facts_of) if kpi.unit == "percent" else facts_of
                kopened = {quarter: _percent_scale(found) for quarter, found in opened.items()} if kpi.unit == "percent" else opened
                held = {(v["fiscal_year"], v["fiscal_period"]): v for v in self.values.values()
                        if v["group_key"] == key and v["kpi_key"] == kpi.key and v["fiscal_period"] != "FY"}
                readings = {quarter: float(v["value"]) for quarter, v in held.items()
                            if v["validation_status"] == "verified" and not (v.get("locator") or {}).get("tagged")}
                if split:
                    records += self._exact_lines(held, readings, quarters, facts_of)  # currency lines: no percent view
                    continue
                missing = [quarter for quarter in newest_first if quarter not in held
                           or (held[quarter]["validation_status"] == "needs_review" and not held[quarter].get("owner_decision"))]
                if not missing:
                    continue
                # The tag: one found before (kept on the figures it filled), else learned from the releases' readings,
                # else, for a KPI the releases never state (Alphabet's revenue backlog), picked from the report's tags.
                tag = next((found for v in held.values() if (found := tag_from_locator(v.get("locator") or {}))), None)
                how = "tagged as in the quarters the release states it"
                if tag is None and kpi.key in picked:
                    tag, how = picked[kpi.key], "the releases do not state it"
                    if any(quarter in quarters and tag in kfacts(quarter) and not agrees(kfacts(quarter)[tag], value)
                           for quarter, value in readings.items()):
                        continue  # a release gave a different figure: not this tag
                elif tag is None and len(readings) >= 2:
                    known = [quarter for quarter in newest_first if quarter in readings and quarter in quarters][:6]
                    tag = learn_tag({quarter: readings[quarter] for quarter in known}, {quarter: kfacts(quarter) for quarter in known})
                elif tag is None:
                    unread.append((group, kpi, key))
                    continue
                if tag is None:
                    continue
                template = next((v for v in held.values() if v["validation_status"] == "verified"), None) or self._kpi_template(group, kpi, key)
                gaps = 0
                for quarter in newest_first:
                    if quarter not in missing:
                        # Never past a quarter whose report (when already opened) tags a different figure.
                        if quarter in readings and tag in kopened.get(quarter, {}) and not agrees(kopened[quarter][tag], readings[quarter]):
                            break
                        gaps = 0
                        # A release's rounded figure ("over $460 billion") gives way to the report's exact one.
                        fact = ((kfacts(quarter) if quarter in newest_first[:4] else kopened.get(quarter, {})).get(tag)
                                if quarter in quarters else None)
                        reading = held.get(quarter)
                        # Only the same figure, more exactly: a release's 3.65% never gives way to a report's 3.
                        if (fact is not None and quarter in readings and fact != readings[quarter] and reading
                                and agrees(fact, readings[quarter]) and not reading.get("owner_decision")):
                            ref, end = quarters[quarter]
                            records.append({**reading, "value": fact, "source_accession": ref.accession, "source_url": ref.source_url,
                                            "filed_at": ref.filed.isoformat(),
                                            "notes": [f"the {ref.form}'s exact figure; the release gave {(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                                            "locator": {"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                                        "instant": tag.instant}})
                        continue
                    fact = kfacts(quarter).get(tag)
                    if fact is None:
                        gaps += 1
                        if gaps >= 2:
                            break  # older reports do not tag it
                        continue
                    gaps = 0
                    ref, end = quarters[quarter]
                    records.append(self._record(
                        group_key=key, group_label=template["group_label"], group_kind=template["group_kind"],
                        group_order=template["group_order"], kpi_key=kpi.key, kpi_label=template["kpi_label"],
                        kpi_order=template["kpi_order"], unit=template["unit"], value=fact, currency=template.get("currency"),
                        method="ai", validation_status="verified", reconciliation_error_pct=None,
                        notes=[f"from the {ref.form}'s XBRL, {how}"],
                        ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=end,
                        locator={"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                 "instant": tag.instant},
                    ))
            if split:
                records += self._year_ago_lines(group, key, quarters, records)
                records += self._exact_periods(group, key, quarters, facts_of, records)
        return records

    def _exact_periods(self, group, key: str, quarters, facts_of, fresh: list[dict]) -> list[dict]:
        """A flagged breakdown quarter whose every line is its report's exact tagged figure (for a Q4, the 10-K's year less
        three settled quarters) is settled: KKR tags each of its asset management segment's five revenue lines to the
        thousand but not their total, so no total proves them. A quarter whose lines, with those its report tags and its
        reading missed or misread, then add up (each parent to its lines, the top lines to the total) takes them all.
        A line's tags are those that equal its readings in at least two quarters and never differ; a total the group
        lists must be its lines added up, to its rounding."""
        rows = {value_key(v): v for v in self.values.values() if v["group_key"] == key and v["validation_status"] != "rejected"
                and v["fiscal_period"] != "FY"}
        rows.update({value_key(v): v for v in fresh if v["group_key"] == key})
        by_period: dict[tuple[str, str], dict[str, dict]] = {}
        for v in rows.values():
            by_period.setdefault((v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
        waiting = sorted(quarter for quarter, lines in by_period.items() if quarter in quarters and any(
            v["validation_status"] == "needs_review" and not v.get("owner_decision") for v in lines.values()))
        if not waiting:
            return []
        tags = {}
        for kpi in group.kpis:
            if kpi.key == group.total_kpi:
                continue
            readings = {quarter: float(lines[kpi.key]["value"]) for quarter, lines in by_period.items()
                        if quarter in quarters and kpi.key in lines}
            # A tag that equals the readings in two quarters at least, and twice as often as it differs: one misread
            # quarter must not keep the others from being settled.
            agree: dict = {}
            differ: dict = {}
            for quarter in sorted(readings, reverse=True)[:12]:
                for tag, fact in facts_of(quarter).items():
                    if fact == readings[quarter]:
                        agree[tag] = agree.get(tag, 0) + 1
                    elif readings[quarter] and abs(fact / readings[quarter] - 1) < 0.2:  # the same series, another figure
                        differ[tag] = differ.get(tag, 0) + 1
            tags[kpi.key] = [tag for tag, count in agree.items() if count >= 2 and count >= 2 * differ.get(tag, 0)]
        records = []
        settled = {"another part of this breakdown needs review", "confirmed by review", "settled by the checks", *_UNANCHORED}
        parents = {kpi.key: kpi.parent for kpi in group.kpis if kpi.parent}

        def tagged(quarter, kpi: str) -> float | None:
            """The quarter's exact figure of a line: its learned tag in the report, or for a Q4 its 10-K's year less three
            verified quarters."""
            facts = facts_of(quarter)
            found = next((facts[tag] for tag in tags.get(kpi, []) if tag in facts), None)
            if found is not None or quarter[1] != "Q4":
                return found
            nine = [by_period.get((quarter[0], period), {}).get(kpi) for period in ("Q1", "Q2", "Q3")]
            if any(v is None or v["validation_status"] != "verified" for v in nine):
                return None
            ref, end = quarters[quarter]
            try:
                annual = quarter_facts(self._xbrl(ref).facts.to_dataframe(), end, (330, 400))
            except Exception:  # noqa: BLE001 - an unreadable report proves nothing
                return None
            return next((annual[tag] - sum(float(v["value"]) for v in nine) for tag in tags.get(kpi, []) if tag in annual), None)

        def whole(figures: dict[str, float], total: float | None) -> bool:
            """Each parent is its lines added up, and the lines not inside another add up to the total."""
            for parent in {parents[kpi] for kpi in figures if parents.get(kpi) in figures}:
                lines = sum(value for kpi, value in figures.items() if parents.get(kpi) == parent)
                if abs(figures[parent] - lines) > _rounding(figures[parent]):
                    return False
            top = sum(value for kpi, value in figures.items() if kpi != group.total_kpi and parents.get(kpi) not in figures)
            return total is None or abs(total - top) <= _rounding(total)

        # A quarter missing a line its report tags is completed (Revenue by Product at JPMorgan, read from a 3 MB
        # supplement whose segment pages repeat each row's name).
        listed = [kpi.key for kpi in group.kpis if kpi.key != group.total_kpi and tags.get(kpi.key)]
        waiting = sorted(set(waiting) | {quarter for quarter, lines in by_period.items() if quarter in quarters
                                         and quarter[1] != "FY" and len(lines) >= 2 and any(kpi not in lines for kpi in listed)})
        for quarter in waiting:
            lines = by_period[quarter]
            parts = {kpi: v for kpi, v in lines.items() if kpi != group.total_kpi}
            if len(parts) < 2:
                continue
            total_row = lines.get(group.total_kpi)
            total = float(total_row["value"]) if total_row else next(
                (float(t) for v in parts.values() if isinstance(t := (v.get("locator") or {}).get("total"), (int, float))), None)
            figures = {kpi: tagged(quarter, kpi) for kpi in parts}
            if any(value is None for value in figures.values()):
                continue
            added = {kpi: value for kpi in listed if kpi not in parts and (value := tagged(quarter, kpi)) is not None}
            changed = {kpi for kpi, value in figures.items() if value != float(parts[kpi]["value"])}
            if not whole({**figures, **added}, total):
                # The lines as read, when each is its report's figure and they add up (KKR's asset management lines).
                if changed or not whole(figures, total):
                    continue
                added = {}
            if not changed and not added and all(v["validation_status"] == "verified" for v in parts.values()):
                continue
            ref, end = quarters[quarter]
            for kpi, v in lines.items():
                notes = [note for note in v.get("notes") or [] if note not in settled and not _is_jump(note)]
                note = (f"the {ref.form}'s exact figure; the release gave {float(v['value']):,.15g}" if kpi in changed
                        else f"the {ref.form}'s exact figure; every line of the quarter is tagged")
                record = {**v, "validation_status": "verified", "notes": [*notes, note],
                          **({"value": figures[kpi]} if kpi in changed else {})}
                records.append(record)
                lines[kpi] = record  # the year's Q4 is worked out from it
            for kpi_key, value in added.items():
                kpi = next(item for item in group.kpis if item.key == kpi_key)
                shape = next((v for v in rows.values() if v["kpi_key"] == kpi_key), None) or self._kpi_template(group, kpi, key)
                record = self._record(
                    group_key=key, group_label=shape["group_label"], group_kind=shape["group_kind"],
                    group_order=shape["group_order"], kpi_key=kpi_key, kpi_label=shape["kpi_label"],
                    kpi_order=shape["kpi_order"], unit=shape["unit"], value=value, currency=shape.get("currency"),
                    method="ai", validation_status="verified", reconciliation_error_pct=None,
                    notes=[f"from the {ref.form}'s XBRL; the release's reading missed it"],
                    ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=end, locator={"tagged": True})
                records.append(record)
                lines[kpi_key] = record
        if records:
            self.log(f"{self.symbol}: {len(records)} breakdown values settled by their reports' exact tagged figures")
        return records

    def _year_ago_lines(self, group, key: str, quarters, fresh: list[dict]) -> list[dict]:
        """A breakdown's periods before its first release, from the reports' comparative columns: Adobe's FY2025 10-Qs
        tag FY2024's customer groups beside their own, its 10-K the two years before. A period is filled only when every
        line's tag (the one its own quarter's figure carries; a 10-K's, its Q3's) has a figure in it, and never over one
        held; a Q4 is then worked out from its year."""
        rows = {value_key(v): v for v in self.values.values() if v["group_key"] == key and v["validation_status"] != "rejected"}
        rows.update({value_key(v): v for v in fresh if v["group_key"] == key})
        by_period: dict[tuple[str, str], dict[str, dict]] = {}
        for v in rows.values():
            by_period.setdefault((v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
        records: list[dict] = []
        for quarter in sorted(by_period):
            if quarter not in quarters or quarter[1] == "FY":
                continue
            annual = quarter[1] == "Q4"
            source = by_period.get((quarter[0], "Q3") if annual else quarter) or {}
            tags = {kpi: tag for kpi, v in source.items() if (tag := tag_from_locator(v.get("locator") or {}))}
            # The lines the period has (a listed total no release gives, Adobe's, is not one), every one tagged.
            if len(tags) < 2 or set(tags) != set(source):
                continue
            ref, end = quarters[quarter]
            try:
                frame = self._xbrl(ref).facts.to_dataframe()
            except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                continue
            year = int(quarter[0])
            for _back in range(2 if annual else 1):
                found = year_ago_facts(frame, end, (330, 400) if annual else (80, 100))
                if found is None:
                    break
                end, facts = found
                year -= 1
                target = (str(year), "FY" if annual else quarter[1])
                if not all(tag in facts for tag in tags.values()):
                    break
                if target in by_period:
                    continue
                by_period[target] = {}
                for kpi, tag in tags.items():
                    record = {**source[kpi], "fiscal_year": target[0], "fiscal_period": target[1], "period_end": end.isoformat(),
                              "value": facts[tag], "method": "ai", "validation_status": "verified",
                              "reconciliation_error_pct": None, "source_accession": ref.accession, "source_url": ref.source_url,
                              "filed_at": ref.filed.isoformat(),
                              "notes": [f"the {ref.form}'s exact figure; its column for {target[1]} {target[0]}"],
                              "locator": {"tagged": True, "concept": tag.concept, "dimensions": dict(tag.dimensions),
                                          "instant": tag.instant, "year_ago": True}}
                    record.pop("owner_decision", None)
                    by_period[target][kpi] = record
                    records.append(record)
        if records:
            self.log(f"{self.symbol}: {len(records)} breakdown values from the reports' year-ago columns")
        return records

    def _exact_lines(self, held: dict, readings: dict, quarters, facts_of) -> list[dict]:
        """A release breakdown's rounded lines, each replaced by its report's exact tagged figure (Adobe's customer
        groups, to $10 million in its releases, to the dollar in its 10-Qs). Each quarter takes the one learned tag its
        own report carries, so a line the company retagged is followed across layouts. The line keeps its breakdown's
        total and the evidence that settled it."""
        known = [quarter for quarter in readings if quarter in quarters]
        tags = learn_tags({quarter: readings[quarter] for quarter in known}, {quarter: facts_of(quarter) for quarter in known})
        tags += [found for v in held.values() if (found := tag_from_locator(v.get("locator") or {})) and found not in tags]
        records = []
        for quarter in known:
            present = [tag for tag in tags if tag in facts_of(quarter)]
            reading = held[quarter]
            if len(present) != 1 or reading.get("owner_decision"):
                continue
            fact = facts_of(quarter)[present[0]]
            if fact == readings[quarter] or not agrees(fact, readings[quarter]):
                continue
            ref, _end = quarters[quarter]
            tag = present[0]
            records.append({**reading, "value": fact, "source_accession": ref.accession, "source_url": ref.source_url,
                            "filed_at": ref.filed.isoformat(),
                            "notes": [*(reading.get("notes") or []), f"the {ref.form}'s exact figure; the release gave "
                                      f"{(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                            "locator": {**(reading.get("locator") or {}), "tagged": True, "concept": tag.concept,
                                        "dimensions": dict(tag.dimensions), "instant": tag.instant}})
        # A Q4 the 10-K tags only for the year (Adobe's FY2025): the year's exact figure less the three exact quarters,
        # when exactly one of the year's figures leaves the release's rounded Q4.
        exact = {(v["fiscal_year"], v["fiscal_period"]): float(v["value"]) for v in [*held.values(), *records]
                 if (v.get("locator") or {}).get("tagged") and v["validation_status"] == "verified"}
        for quarter in known:
            year = quarter[0]
            nine = [exact.get((year, period)) for period in ("Q1", "Q2", "Q3")]
            if quarter[1] != "Q4" or quarter in exact or None in nine or held[quarter].get("owner_decision"):
                continue
            ref, end = quarters[quarter]
            try:
                annual = quarter_facts(self._xbrl(ref).facts.to_dataframe(), end, (330, 400))
            except Exception:  # noqa: BLE001 - an unreadable report gives nothing
                continue
            fits = {fact - sum(nine) for fact in annual.values() if agrees(fact - sum(nine), readings[quarter])}
            if len(fits) != 1:
                continue
            reading, value = held[quarter], fits.pop()
            records.append({**reading, "value": value, "notes": [*(reading.get("notes") or []),
                            f"the {ref.form}'s exact year less its three quarters; the release gave "
                            f"{(reading.get('locator') or {}).get('value_text') or readings[quarter]}"],
                            "locator": {**(reading.get("locator") or {}), "tagged": True}})
        return records
