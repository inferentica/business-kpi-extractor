"""Keeping each KPI one series over time: refiled periods, renamed lines, restatements and mislabelled quarters."""
from __future__ import annotations

from datetime import date, timedelta
from itertools import permutations

from ..derive import balancing
from ..sec import FilingRef
from ..segment_income import GROUP_KEY as INCOME_GROUP
from .shared import (AI_GROUP_PREFIX, _ANNUAL_HEADER, _QUARTER_HEADER, _annual_header, _days, _is_jump, _same,
                     _year_ago_cells, value_key)

def _quarter_column(value: dict) -> bool:
    """Whether a value was read from a column headed for three months, which no yearly total contradicts."""
    locator = value.get("locator") or {}
    header = str(locator.get("header") or "")
    return bool(_QUARTER_HEADER.search(header)) and not _ANNUAL_HEADER.search(header)


def _restated_for(restated: dict, group: str, end: str) -> dict[str, float]:
    """The later comparatives for a quarter, whose restated end can differ by days (52/53-week years)."""
    return next((values for (key, other), values in restated.items() if key == group and abs(_days(other, end)) <= 12), {})



class SeriesIdentity:
    """Keeping each KPI one series over time: refiled periods, renamed lines, restatements and mislabelled quarters."""

    def _harmonize_labels(self) -> None:
        """One name per XBRL series across every period: the company's approved name, else its latest label."""
        latest: dict[tuple[str, str], dict] = {}
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            key = (value["group_key"], value["kpi_key"])
            if value["period_end"] >= latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        group_latest: dict[str, dict] = {}
        for (group_key, _kpi), value in latest.items():
            if value["period_end"] >= group_latest.get(group_key, {}).get("period_end", ""):
                group_latest[group_key] = value
        renamed = []
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            named = self.names.get(value["group_key"]) or {}
            label = named.get("label") or group_latest[value["group_key"]]["group_label"]
            member = ((named.get("members") or {}).get(value["kpi_key"])
                      or latest[(value["group_key"], value["kpi_key"])]["kpi_label"])
            if (label, member) != (value["group_label"], value["kpi_label"]):
                renamed.append({**value, "group_label": label, "kpi_label": member})
        for start in range(0, len(renamed), 400):
            self._store(values=renamed[start:start + 400])

    def _refile_annuals(self) -> None:
        """Repairs full-year figures stored as Q4 before these checks existed: every AI value from that filing moves
        to FY, and the Q4 rows are rejected so Q4 is derived as FY − Q1 − Q2 − Q3. A reading from a column headed for
        three months stays the quarter it is (Apple's FY2019 Q4 release: its product-type table was moved to FY with
        the release's yearly readings, and Q4 came out at −$110.8 billion); one moved before is moved back."""
        self._unfile_quarters()
        annual_totals = {v["period_end"]: float(v["locator"]["total"]) for v in self.values.values()
                         if v["method"] == "xbrl" and v["fiscal_period"] == "FY" and (v.get("locator") or {}).get("total")}
        misfiled = set()
        for v in self.values.values():
            total = (v.get("locator") or {}).get("total")
            if v["method"] != "ai" or v["fiscal_period"] != "Q4" or v["validation_status"] == "rejected" or not total:
                continue
            total = float(total)
            annual = next((a for end, a in annual_totals.items() if abs(_days(end, v["period_end"])) <= 12), None)
            header = str((v.get("locator") or {}).get("header") or "")
            if (annual and abs(total - annual) <= abs(annual) * 0.01) or _annual_header(header):
                misfiled.add((v["source_accession"], v["fiscal_year"]))
        if not misfiled:
            return
        moved = []
        for v in list(self.values.values()):
            if v["method"] == "ai" and v["fiscal_period"] == "Q4" and (v["source_accession"], v["fiscal_year"]) in misfiled \
                    and not _quarter_column(v):
                # A jump flagged while the year was mistaken for a quarter says nothing about the year.
                notes = [note for note in (v.get("notes") or []) if not _is_jump(note)]
                status = "verified" if v["validation_status"] == "needs_review" and not notes else v["validation_status"]
                moved.append({**v, "fiscal_period": "FY", "validation_status": status,
                              "notes": [*notes, "full-year figures, re-filed from Q4"]})
                moved.append({**v, "validation_status": "rejected", "notes": ["full-year figures filed as Q4; see FY"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])
        self.log(f"{self.symbol}: re-filed {len(misfiled)} annual report(s) stored as Q4")

    def _unfile_quarters(self) -> None:
        """A quarter's figures moved to FY with their release's yearly readings go back to Q4, the year's derived rows
        rejected so the year is worked out again."""
        back = []
        for v in list(self.values.values()):
            if v["method"] != "ai" or v["fiscal_period"] != "FY" or v["validation_status"] == "rejected" \
                    or "full-year figures, re-filed from Q4" not in (v.get("notes") or []) or not _quarter_column(v):
                continue
            notes = [note for note in v.get("notes") or [] if note != "full-year figures, re-filed from Q4"]
            back.append({**v, "fiscal_period": "Q4", "validation_status": "verified", "notes": notes})
            back.append({**v, "validation_status": "rejected", "notes": ["a quarter's figure, moved back to Q4"]})
            derived = self.values.get((v["group_key"], v["kpi_key"], v["fiscal_year"], "Q4"))
            if derived and derived["method"] == "derived":
                self.values.pop(value_key(derived), None)
        for start in range(0, len(back), 400):
            self._store(values=back[start:start + 400])
        if back:
            self.log(f"{self.symbol}: {len(back) // 2} quarterly figures moved back from FY to Q4")

    def _join_custom_axes(self) -> None:
        """A company's own early axis continued by a standard one (Nvidia's 2016–18 "Revenue by Major Market" became its
        Revenue by Product): its periods join the standard group when most of its rows are rows of that group and every
        period the two share holds the same figures. No AI involved."""
        groups: dict[str, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "xbrl" and v["validation_status"] != "rejected":
                groups.setdefault(v["group_key"], []).append(v)
        standard = {key: rows for key, rows in groups.items() if not key.startswith(("x_", AI_GROUP_PREFIX))}
        moved = []
        for key, rows in groups.items():
            if not key.startswith("x_"):
                continue
            own = {v["kpi_key"] for v in rows}
            shared, target = max(((len(own & {v["kpi_key"] for v in other}), name) for name, other in standard.items()),
                                 default=(0, None))
            if target is None or shared < 2 or shared * 2 < len(own):
                continue
            theirs = {(v["fiscal_year"], v["fiscal_period"], v["kpi_key"]): float(v["value"]) for v in standard[target]}
            both = {(v["fiscal_year"], v["fiscal_period"]) for v in standard[target]}
            if any((v["fiscal_year"], v["fiscal_period"]) in both
                   and not _same(theirs.get((v["fiscal_year"], v["fiscal_period"], v["kpi_key"]), float("nan")), float(v["value"]))
                   for v in rows):
                continue  # a shared period read differently: two views after all
            latest = max(standard[target], key=lambda v: v["period_end"])
            for v in rows:
                if (v["fiscal_year"], v["fiscal_period"]) not in both:
                    moved.append({**v, "group_key": target, "group_label": latest["group_label"],
                                  "group_order": latest["group_order"]})
                moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {target}"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])

    def _unify_series(self) -> None:
        """One key per business across a company's whole XBRL history, so Q4 = year − quarters always lines up.
        Two kinds of evidence link keys: the same XBRL element (a label change: "Dynamics" became "Dynamics products and
        cloud services"), and a member whose restated prior-year figure equals, to the unit, what another key reported
        for that year (a new element: Microsoft's "Xbox" restates FY2025 "Gaming"). Microsoft moved Search between two
        elements and back, so neither alone suffices; together they link the whole history. A linked set that ever has
        two of its keys in one period is two businesses after all and is left alone. The series takes its latest key,
        so it carries the latest name."""
        rows = [v for v in self.values.values() if v["method"] == "xbrl" and v["validation_status"] != "rejected"]
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(node):
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a, b):
            parent[find(a)] = find(b)

        by_element: dict[tuple[str, str], tuple[str, str]] = {}
        index: dict[tuple[str, bool], list[dict]] = {}
        for v in rows:
            node = (v["group_key"], v["kpi_key"])
            find(node)
            element = (v.get("locator") or {}).get("member")
            if element:
                if (v["group_key"], element) in by_element:
                    union(node, by_element[(v["group_key"], element)])
                else:
                    by_element[(v["group_key"], element)] = node
            index.setdefault((v["group_key"], v["fiscal_period"] == "FY"), []).append(v)
        for v in rows:
            prior = (v.get("locator") or {}).get("prior")
            if not prior:
                continue
            target = (date.fromisoformat(v["period_end"][:10]) - timedelta(days=365)).isoformat()
            matches = {(o["group_key"], o["kpi_key"]) for o in index.get((v["group_key"], v["fiscal_period"] == "FY"), [])
                       if abs(_days(o["period_end"], target)) <= 12 and _same(float(o["value"]), float(prior))}
            if len(matches) == 1:
                union((v["group_key"], v["kpi_key"]), matches.pop())
        components: dict[tuple[str, str], list[dict]] = {}
        for v in rows:
            components.setdefault(find((v["group_key"], v["kpi_key"])), []).append(v)
        rekeyed = []
        for members in components.values():
            keys = {v["kpi_key"] for v in members}
            if len(keys) < 2:
                continue
            periods = [(v["fiscal_year"], v["fiscal_period"]) for v in members]
            if len(periods) != len(set(periods)):
                continue  # two of its keys in one period: different businesses after all
            canonical = max(members, key=lambda v: (v["period_end"], v["fiscal_period"] == "FY"))["kpi_key"]
            for v in members:
                if v["kpi_key"] != canonical:
                    rekeyed.append({**v, "kpi_key": canonical})
                    rekeyed.append({**v, "validation_status": "rejected", "notes": [f"continued as {canonical}"]})
        for start in range(0, len(rekeyed), 400):
            self._store(values=rekeyed[start:start + 400])
        if rekeyed:
            self.log(f"{self.symbol}: {len(rekeyed) // 2} XBRL values joined to their series")

    def _adopt_restated_quarters(self) -> None:
        """A year's quarter that is missing or lacks a row of the year's own 10-K (Alphabet's 2019 10-Qs grouped Search
        and YouTube as "Google properties" and left "Google other" untagged in Q1) is taken from the following year's
        10-Q for the same quarter, which restates it on the year's layout: only when that restated quarter has exactly
        the year's rows and adds up to the quarter's reported revenue. The year's Q4 then follows on one layout."""
        rows: dict[tuple[str, str, str], dict[str, dict]] = {}
        revenue: dict[tuple[str, str, str], tuple[float, str]] = {}
        for v in self.values.values():
            if v["method"] != "xbrl" or v["validation_status"] != "verified" or v["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            rows.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
            total = (v.get("locator") or {}).get("total")
            if isinstance(total, (int, float)):
                # Each group's own total: segment operating income does not add up to revenue.
                revenue.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), (float(total), v["period_end"]))
        changed = []
        # A year with quarters but no figure of its own (Nvidia's FY2018 10-K tags no regions for the year) takes the
        # year as the following 10-K restates it, when that restatement adds up to the revenue it restates.
        for (group, year, period), following in list(rows.items()):
            previous = str(int(year) - 1)
            if period != "FY" or (group, previous, "FY") in rows or not any((group, previous, q) in rows for q in ("Q1", "Q2", "Q3")):
                continue
            priors = {key: (row.get("locator") or {}).get("prior") for key, row in following.items()}
            restated_total = next((float(t) for row in following.values()
                                   if isinstance(t := (row.get("locator") or {}).get("prior_total"), (int, float))), None)
            end = next((f["period_end"] for f in self.filings.values() if f.get("document_role") == "periodic_report"
                        and f.get("fiscal_year") == previous and f.get("fiscal_period") == "FY" and f.get("period_end")), None)
            if (not all(isinstance(v, (int, float)) for v in priors.values()) or restated_total is None or end is None
                    or abs(sum(priors.values()) - restated_total) > abs(restated_total) * 0.001):
                continue
            year_rows = {key: {**row, "fiscal_year": previous, "period_end": end, "value": float(priors[key]),
                               "reconciliation_error_pct": (sum(priors.values()) - restated_total) / restated_total,
                               "locator": {"total": restated_total, "member": (row.get("locator") or {}).get("member"),
                                           "restated_by": row.get("source_accession")},
                               "notes": ["as restated in the following year's filing"]} for key, row in following.items()}
            rows[(group, previous, "FY")] = year_rows
            changed += year_rows.values()
        for (group, year, period), annual in list(rows.items()):
            if period != "FY":
                continue
            for quarter in ("Q1", "Q2", "Q3"):
                current = rows.get((group, year, quarter)) or {}
                if current and not {key for key in set(annual) - set(current) if not balancing(key)}:
                    continue  # on the year's layout, or finer (Nvidia's 10-Qs list regions its 10-K folds into Other)
                following = rows.get((group, str(int(year) + 1), quarter)) or {}
                # A business the following year added (Amazon's physical stores, from Whole Foods in August 2017) shows
                # as nothing in the restated quarter: not a row of the year's layout, so left out.
                following = {key: row for key, row in following.items()
                             if key in annual or (row.get("locator") or {}).get("prior") not in (0, 0.0, None)}
                priors = {key: (row.get("locator") or {}).get("prior") for key, row in following.items()}
                if not priors or set(priors) != set(annual) or not all(isinstance(v, (int, float)) for v in priors.values()):
                    continue
                # The restated rows must add up to revenue: the quarter's own when its report was read, else the
                # revenue the restating report gives for that quarter.
                restated_total = next((float(t) for row in following.values()
                                       if isinstance(t := (row.get("locator") or {}).get("prior_total"), (int, float))), None)
                reported = revenue.get((group, year, quarter))
                if reported is None and restated_total is not None:
                    # The quarter's own report was read but kept nothing: its record still knows when the quarter ended.
                    end = next((f["period_end"] for f in self.filings.values() if f.get("document_role") == "periodic_report"
                                and f.get("fiscal_year") == year and f.get("fiscal_period") == quarter and f.get("period_end")),
                               (date.fromisoformat(next(iter(following.values()))["period_end"][:10]) - timedelta(days=364)).isoformat())
                    reported = (restated_total, end)
                if reported is None or abs(sum(priors.values()) - reported[0]) > abs(reported[0]) * 0.001:
                    continue
                note = "as restated in the following year's filing"
                changed += [{**row, "fiscal_year": year, "fiscal_period": quarter, "period_end": reported[1],
                             "value": float(priors[key]), "reconciliation_error_pct": (sum(priors.values()) - reported[0]) / reported[0],
                             "locator": {"total": reported[0], "member": (row.get("locator") or {}).get("member"),
                                         "restated_by": row.get("source_accession")},
                             "notes": [note]} for key, row in following.items()]
                changed += [{**row, "validation_status": "rejected", "notes": ["replaced by the following year's restated layout"]}
                            for key, row in current.items() if key not in priors]
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])
        adopted = sum(1 for v in changed if v["validation_status"] == "verified")
        if adopted:
            self.log(f"{self.symbol}: {adopted} quarterly values taken from the following year's restated layout")

    def _unify_release_series(self, releases: list[FilingRef]) -> None:
        """A line a release renamed (Alphabet's "Google other" became "Google subscriptions, platforms, and devices" in
        Q4 2023) is one series under its new name: the release's year-ago cell in the new line's row equals what the old
        line reported a year earlier. Without it a year's quarters carry two names and the year cannot be added up. The
        release comes from the filing archive; no AI involved."""
        by_accession = {ref.accession: ref for ref in releases}
        groups: dict[str, list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] == "verified" and v["group_kind"] != "metric":
                groups.setdefault(v["group_key"], []).append(v)
        moved = []
        for group, rows in groups.items():
            quarters = [v for v in rows if v["fiscal_period"] != "FY"]
            if not quarters:
                continue
            first = {key: min(v["period_end"] for v in quarters if v["kpi_key"] == key) for key in {v["kpi_key"] for v in quarters}}
            last = {key: max(v["period_end"] for v in quarters if v["kpi_key"] == key) for key in first}
            start, end = min(first.values()), max(last.values())
            ended = [key for key in first if last[key] < end]
            for key in [key for key in first if first[key] > start] if ended else []:
                opening = next(v for v in quarters if v["kpi_key"] == key and v["period_end"] == first[key])
                ref = by_accession.get(opening.get("source_accession") or "")
                if ref is None or (opening.get("locator") or {}).get("row") is None:
                    continue
                target = (date.fromisoformat(opening["period_end"][:10]) - timedelta(days=365)).isoformat()
                before = {old: float(v["value"]) for v in quarters for old in ended
                          if v["kpi_key"] == old and abs(_days(v["period_end"], target)) <= 12}
                try:
                    cells = _year_ago_cells(self._document(ref), opening["locator"], float(opening["value"]))
                except Exception:  # noqa: BLE001 - an unreadable release links nothing
                    continue
                matches = [old for old, value in before.items() if any(abs(cell - value) <= abs(value) * 1e-4 for cell in cells)]
                if len(matches) != 1 or any(v["kpi_key"] == key for v in rows for o in rows
                                            if o["kpi_key"] == matches[0] and o["fiscal_year"] == v["fiscal_year"]
                                            and o["fiscal_period"] == v["fiscal_period"]):
                    continue  # no single predecessor, or the two lines share a period: different businesses
                for v in rows:
                    if v["kpi_key"] == matches[0]:
                        moved.append({**v, "kpi_key": key, "kpi_label": opening["kpi_label"]})
                        moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {key}"]})
        for start_at in range(0, len(moved), 400):
            self._store(values=moved[start_at:start_at + 400])
        if moved:
            self.log(f"{self.symbol}: {len(moved) // 2} release values joined to their renamed series")

    def _fix_mislabelled_quarters(self) -> None:
        """A filer's XBRL can attach figures to the wrong members (AMD's 2023 and 2024 10-Qs tag Data Center's $2,337M as
        Client). A later filing's comparatives then contradict them: the same member restated with a value another member
        of that quarter holds. The quarters of that year are relabelled with the one mapping that every later comparative
        and the year's own 10-K allow (no member's Q4 revenue below zero); if the evidence allows none or several, the
        mapping found for another group of the same filings (operating income takes revenue's) is used when it fits, else
        they are flagged, so swapped names are never served. Years go newest first: a relabelled year's own comparatives
        are the evidence for the year before (AMD's 2024 10-Qs restate 2023 with the same wrong tags)."""
        held = "member labels contradict later filings"
        done: set[tuple[str, str]] = set()
        for _round in range(6):
            rows = [v for v in self.values.values() if v["method"] == "xbrl" and (
                v["validation_status"] == "verified" or held in (v.get("notes") or []))]
            by_period: dict[tuple[str, str, str], dict[str, dict]] = {}
            for v in rows:
                by_period.setdefault((v["group_key"], v["fiscal_year"], v["fiscal_period"]), {})[v["kpi_key"]] = v
            # Later comparatives: (group, quarter end) → {key: restated value}.
            restated: dict[tuple[str, str], dict[str, float]] = {}
            for v in rows:
                prior = (v.get("locator") or {}).get("prior")
                if prior is not None and v["fiscal_period"] != "FY" and v["validation_status"] == "verified":
                    end = (date.fromisoformat(v["period_end"][:10]) - timedelta(days=365)).isoformat()
                    restated.setdefault((v["group_key"], end), {})[v["kpi_key"]] = float(prior)
            contradicted = {item: quarters for item, quarters in self._contradicted_years(by_period, restated).items()
                            if item not in done}
            flagged = {(v["group_key"], v["fiscal_year"]) for v in rows if held in (v.get("notes") or [])
                       and v["fiscal_period"] != "FY" and (v["group_key"], v["fiscal_year"]) not in done}
            for group, year in flagged - set(contradicted):
                quarters = [q for q in (by_period.get((group, year, p)) for p in ("Q1", "Q2", "Q3")) if q]
                if quarters and len({frozenset(q) for q in quarters}) == 1:
                    contradicted[(group, year)] = quarters
            if not contradicted:
                break
            # Newest year first; revenue before operating income, whose mapping may be borrowed.
            (group, year), quarters = max(contradicted.items(), key=lambda item: (item[0][1], item[0][0] != INCOME_GROUP))
            done.add((group, year))
            changes = []
            keys = sorted(quarters[0])
            annual = by_period.get((group, year, "FY"), {})
            evidence = [(quarter, {k: x for k, x in _restated_for(restated, group, quarter[keys[0]]["period_end"]).items()
                                   if k in keys}) for quarter in quarters]

            def fits_evidence(mapping: dict[str, str]) -> bool:
                relabelled = [{mapping[k]: float(q[k]["value"]) for k in keys} for q in quarters]
                if any(any(not _same(relabelled[i][k], x) for k, x in found.items()) for i, (_q, found) in enumerate(evidence)):
                    return False
                # A segment's operating income may be a loss in Q4; its revenue may not.
                return group == INCOME_GROUP or set(annual) != set(keys) or not any(
                    float(annual[k]["value"]) - sum(r[k] for r in relabelled) < 0 for k in keys if not balancing(k))

            fits = [mapping for order in permutations(keys) if fits_evidence(mapping := dict(zip(keys, order)))]
            if len(fits) != 1:
                # Another group of the same filings, already on its true names, says which segment each member is.
                named: dict[tuple[str, str], str] = {}
                for v in self.values.values():
                    member = (v.get("locator") or {}).get("member")
                    if v["group_key"] != group and member and v["method"] == "xbrl" and v["validation_status"] == "verified":
                        named[(v.get("source_accession") or "", member)] = v["kpi_key"]
                borrowed = set()
                for quarter in quarters:
                    mapping = {}
                    for key, row in quarter.items():
                        true = named.get((row.get("source_accession") or "", (row.get("locator") or {}).get("member") or ""))
                        mapping[key] = true if true in keys else key
                    borrowed.add(tuple(sorted(mapping.items())))
                if len(borrowed) == 1 and sorted((mapping := dict(next(iter(borrowed)))).values()) == keys and fits_evidence(mapping):
                    fits = [mapping]
            if len(fits) == 1 and any(k != t for k, t in fits[0].items()):
                for quarter in quarters:
                    for key, row in quarter.items():
                        true = fits[0][key]
                        label = next((q[true]["kpi_label"] for q in quarters if true in q), true)
                        notes = [note for note in (row.get("notes") or []) if note != held]
                        changes.append({**row, "kpi_key": true, "kpi_label": label, "validation_status": "verified",
                                        "notes": [*notes, *([] if true == key else ["member labels corrected from later filings"])]})
            elif len(fits) == 1:
                changes += [{**row, "validation_status": "verified", "notes": [n for n in row.get("notes") or [] if n != held]}
                            for quarter in quarters for row in quarter.values() if held in (row.get("notes") or [])]
            elif not any(all(_same(float(q[k]["value"]), x) for k, x in found.items()) for q, found in evidence):
                changes += [{**row, "validation_status": "needs_review",
                             "notes": [*(row.get("notes") or []), *([] if held in (row.get("notes") or []) else [held])]}
                            for quarter in quarters for row in quarter.values()]
            for start in range(0, len(changes), 400):
                self._store(values=changes[start:start + 400])
            if changes:
                self.log(f"{self.symbol}: {len(changes)} XBRL values of {group} {year} relabelled or held from later filings' comparatives")

    @staticmethod
    def _contradicted_years(by_period: dict, restated: dict) -> dict[tuple[str, str], list[dict]]:
        """(group, fiscal year) → its Q1–Q3 rows, for years where a later comparative gives a member a value another
        member of that quarter holds."""
        years: dict[tuple[str, str], list[dict]] = {}
        for (group, year, period), quarter in by_period.items():
            if period == "FY":
                continue
            found = _restated_for(restated, group, next(iter(quarter.values()))["period_end"])
            values = {key: float(row["value"]) for key, row in quarter.items()}
            if any(key in values and not _same(values[key], x) and any(_same(x, other) for k2, other in values.items() if k2 != key)
                   for key, x in found.items()):
                years[(group, year)] = []
        for (group, year) in list(years):
            quarters = [by_period.get((group, year, q)) for q in ("Q1", "Q2", "Q3")]
            quarters = [q for q in quarters if q]
            if not quarters or len({frozenset(q) for q in quarters}) != 1 or len(quarters[0]) > 6:
                del years[(group, year)]
                continue
            years[(group, year)] = quarters
        return years
