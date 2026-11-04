"""The company's KPI list: which documents count, proposing the list, keeping it up each quarter."""
from __future__ import annotations

import re
from datetime import timedelta

from .. import ai
from ..ai import AiResponseError, Spec
from ..control import ControlError
from ..extract import _listed_kpi
from ..sec import FilingRef, classification_excerpt, is_own_document
from ..segment_income import GROUP_KEY as INCOME_GROUP
from ..xbrl import XbrlCandidate
from .shared import AI_GROUP_PREFIX, _DONE, _days, _identity_text, only_adds

# The quarters a refresh or a scheduled run covers; Read filings reaches further (40).
REFRESH_QUARTERS = 20
_NOT_EARNINGS = "classified as not an earnings document"
_CLASSIFIED_PAST_COVER = "classified past the cover 2"  # 2: covers written with &nbsp; too

def keep_current_groups(proposed: Spec, current: Spec) -> Spec:
    """A re-proposed list never silently drops a group the current list tracks (TSMC's platform and geography once
    vanished on a re-read); only the quarterly audit retires what a company stopped reporting."""
    keys = {group.key for group in proposed.groups}
    labels = {(group.kind, group.label.lower()) for group in proposed.groups}
    kept = [group for group in current.groups
            if group.key not in keys and (group.kind, group.label.lower()) not in labels]
    if not kept:
        return proposed
    groups = [*(group.model_dump() for group in proposed.groups), *(group.model_dump() for group in kept)]
    return Spec.model_validate({"groups": groups[:ai.MAX_GROUPS], "names": proposed.names})

_REVENUE_SUFFIX = re.compile(r"(?<=.)(netrevenues?|revenues?|netsales|sales)$")

def repeats_xbrl(group, covered: list[set[str]]) -> bool:
    """Whether a group (of any kind) names the same rows as a breakdown the company still tags in XBRL (Meta's Family
    of Apps and Reality Labs, once added as "metrics"): read by code with its whole history, never again from
    releases."""
    # A subtotal row the release adds ("Total Optum" beside its three segments, UnitedHealth) does not make it new.
    # "Agents and Infra Revenue" is the 10-Q's "Agents and Infra" (Microsoft's release names each line so).
    names = {_REVENUE_SUFFIX.sub("", _identity_text(kpi.label)) for kpi in group.kpis
             if kpi.key != group.total_kpi and not _identity_text(kpi.label).startswith("total")}
    tagged = [{_REVENUE_SUFFIX.sub("", member) for member in members} for members in covered]
    # Lines of one breakdown beside its segment's total from another (SpaceX's "SI revenues" beside its two lines).
    return len(names) >= 2 and (any(names <= members for members in tagged)
                                or (names <= set().union(*tagged) and any(len(names & members) >= 2 for members in tagged)))


def without_xbrl_repeats(spec: Spec, covered: list[set[str]], keep: Spec | None = None) -> Spec:
    """A proposed list without groups that repeat a breakdown still tagged in XBRL; a group the current list already
    has stays (the owner or the audit's history decides it)."""
    kept_keys = {group.key for group in keep.groups} if keep else set()
    groups = [group for group in spec.groups if group.key in kept_keys or not repeats_xbrl(group, covered)]
    if len(groups) == len(spec.groups):
        return spec
    return Spec.model_validate({"groups": [group.model_dump() for group in groups], "names": spec.names})


def apply_maintenance(spec: Spec, answer: ai.Maintenance, covered: list[set[str]] = ()) -> Spec | None:
    """The list with the audit's additions and retirements, or None when nothing changes. Existing keys keep their
    meaning; a new key that collides with an existing one is ignored; a group that repeats a breakdown the company
    still tags in XBRL is not added."""
    groups = [group.model_copy(deep=True) for group in spec.groups]
    by_key = {group.key: group for group in groups}
    changed = False
    for group_key, kpis in answer.add_kpis.items():
        group = by_key.get(group_key)
        if group is None:
            continue
        existing = {kpi.key for kpi in group.kpis}
        for kpi in kpis:
            unit_ok = group.kind == "metric" or kpi.unit == ("percent" if group.kind == "mix" else "currency")
            if kpi.key not in existing and unit_ok and len(group.kpis) < ai.MAX_KPIS_PER_GROUP:
                group.kpis.append(kpi)
                existing.add(kpi.key)
                changed = True
    amounts = {_dimension(group.label) for group in groups if group.kind == "revenue_breakdown"}
    for group in answer.add_groups:
        if group.kind == "mix" and _dimension(group.label) in amounts:
            continue  # shares of a breakdown already tracked as amounts add nothing (TSMC's technology mix)
        if repeats_xbrl(group, list(covered)):
            continue
        if group.key not in by_key and len(groups) < ai.MAX_GROUPS:
            groups.append(group)
            by_key[group.key] = group
            changed = True
    for retired in answer.retire:
        group_key, _, kpi_key = retired.partition(".")
        group = by_key.get(group_key)
        if group and kpi_key and len(group.kpis) > 1 and any(kpi.key == kpi_key for kpi in group.kpis):
            group.kpis = [kpi for kpi in group.kpis if kpi.key != kpi_key]
            if group.total_kpi == kpi_key:
                group.total_kpi = None
            changed = True
    if not changed:
        return None
    return ai.normalize_spec(Spec.model_validate({"groups": [group.model_dump() for group in groups], "names": spec.names}))


def _dimension(label: str) -> str:
    """What a breakdown splits revenue by: "Revenue by Technology Mix" and "Revenue by Technology" → "technology"."""
    words = re.sub(r"\b(wafer|net|revenue|sales|by|mix|share|shares|of|the)\b", " ", label.lower())
    return " ".join(words.split())



class KpiListUpkeep:
    """The company's KPI list: which documents count, proposing the list, keeping it up each quarter."""

    def _curate(self, candidate: XbrlCandidate):
        """The reasoning reader picks the rows that make up revenue; the same rows are reused for later filings with the same rows."""
        cache_key = (candidate.key, ",".join(sorted(candidate.members))[:2000])  # stored under the same key
        if cache_key in self._curations:
            return candidate.group(self._curations[cache_key])  # The reasoning reader's choice, kept between runs
        rows = "\n".join(f"- {key}: {label} = {value:,.0f}" for key, (label, value) in candidate.members.items())
        data = (f"Breakdown: {candidate.label} ({candidate.concept})\nReported total revenue: {candidate.total:,.0f}\n"
                f"Rows (member key: label = amount):\n{rows}")
        try:
            answer = self._ask(ai.prompt(self.company, "(XBRL facts, listed below)", ai.CURATE_TASK, data),
                               "curate", ai.Curation, thinking_first=True, model="flash")
        except AiResponseError:
            return None
        self._curations[cache_key] = answer.members
        self.control.call("store", symbol=self.symbol, filings=[], values=[], curations=[
            {"group_key": candidate.key, "members_key": cache_key[1], "chosen": answer.members}])
        return candidate.group(answer.members)

    def _is_earnings_document(self, ref: FilingRef) -> bool:
        if ref.confirmed:
            return True
        previous = self.filings.get(ref.accession)
        own = any(is_own_document(exhibit) for exhibit in ref.exhibits)
        if previous and not (own and _NOT_EARNINGS in (previous.get("notes") or [])
                             and _CLASSIFIED_PAST_COVER not in (previous.get("notes") or [])):
            return _NOT_EARNINGS not in (previous.get("notes") or [])
        try:
            answer = self._ask(ai.prompt(self.company, classification_excerpt(ref), ai.CLASSIFY_TASK),
                               "classify", ai.Classification, thinking_first=False, model="flash")
        except (AiResponseError, ControlError):
            return False  # nothing is recorded, so the next run classifies it again
        if answer.kind == "other":
            self._store_filing(ref, "skipped", notes=[_NOT_EARNINGS, *([_CLASSIFIED_PAST_COVER] if own else [])])
            return False
        return True

    def _spec(self, row: dict | None, releases: list[FilingRef]) -> tuple[Spec | None, int, bool]:
        # A list the owner edited (Bedrock's KPI templates) is kept as edited, even on a forced run.
        if row and (not self.force or row.get("owner_edited_at")):
            stored = Spec.model_validate({"groups": row["groups"], "names": row.get("names") or {}})
            spec, version = self._capped(stored), int(row["version"])
            if not row.get("owner_edited_at"):
                # A group the extractor listed from a release read before its report (Microsoft's new segments, listed
                # an hour before the 10-Q tagged them) goes once the report's XBRL gives the same rows.
                cleaned = without_xbrl_repeats(spec, [] if self.profile.foreign else self._current_xbrl_members())
                if cleaned is not spec:
                    self._set_aside_dropped(spec, cleaned)
                    spec = cleaned
            if spec.model_dump() != stored.model_dump():
                # A stored list that breaks the rules is repaired once; its filings are then read with the new version.
                version += 1
                self._store_spec(spec, row.get("names") or {}, version,
                                 row.get("source_accession") or (releases[-1].accession if releases else "normalized"))
                self.log(f"{self.symbol}: KPI list v{version}: groups that repeat XBRL or cannot reconcile were changed")
            return spec, version, False
        if not releases:
            return None, 0, False
        latest = releases[-1]
        latest_quarter = [ref for ref in releases if ref.period_end == latest.period_end]
        breakdowns = self._xbrl_breakdowns()
        # Foreign issuers tag XBRL only once a year, so their quarterly breakdowns must still come from releases.
        covered = [] if self.profile.foreign else [
            f"{group['label']}: {', '.join(group['members'].values())}" for group in breakdowns.values()
        ]
        to_name = [
            f"- {key}: {group['label']} (" + "; ".join(f"{member} = {label}" for member, label in group["members"].items()) + ")"
            for key, group in breakdowns.items()
        ]
        try:
            document = self._document(*latest_quarter)
            text = self._prompt_document(document, None)
            # Reasoning first: the KPI list and names shape every later quarter.
            current = Spec.model_validate({"groups": row["groups"]}) if row else None
            spec = self._ask(ai.prompt(self.company, text, ai.PROPOSE_TASK, ai.propose_data(covered, to_name, current)),
                             "propose", Spec, thinking_first=True, model="flash")
        except Exception as error:  # noqa: BLE001
            self.result.errors.append(f"KPI list: {error}"[:300])
            return None, 0, False
        if current is not None:
            spec = keep_current_groups(spec, current)
        # A group the owner kept stays; one the extractor itself listed that repeats an XBRL breakdown (Amazon's product
        # and service sales, listed before XBRL tagged them as a view) goes.
        spec = without_xbrl_repeats(spec, [] if self.profile.foreign else self._current_xbrl_members(),
                                    keep=current if getattr(self, "_owner_edited", False) else None)
        self._set_aside_dropped(current, spec)
        spec = self._capped(spec)
        # Names may only rename breakdowns and members that exist.
        names = {
            key: {"label": name.label, "members": {m: l for m, l in name.members.items() if m in breakdowns[key]["members"]}}
            for key, name in spec.names.items() if key in breakdowns
        }
        version = (int(row["version"]) if row else 0) + 1
        self._store_spec(spec, names, version, latest.accession)
        self.names = names
        self.log(f"{self.symbol}: KPI list v{version} with {sum(len(g.kpis) for g in spec.groups)} KPIs")
        return spec, version, True

    def _set_aside_dropped(self, before: Spec | None, after: Spec) -> None:
        """Figures of a group the list no longer has because it repeats an XBRL breakdown are set aside, as the template
        page's save does, so the breakdown is not shown twice (once from XBRL, once as releases' leftover rows)."""
        if before is None:
            return
        covered = [] if self.profile.foreign else self._current_xbrl_members()
        kept = {group.key for group in after.groups}
        dropped = {f"{AI_GROUP_PREFIX}{group.key}" for group in before.groups
                   if group.key not in kept and repeats_xbrl(group, covered)}
        if not dropped:
            return
        aside = [{**value, "validation_status": "rejected", "notes": ["repeats a breakdown read from XBRL"]}
                 for value in self.values.values()
                 if value["group_key"] in dropped and value["method"] in ("ai", "derived") and value["validation_status"] != "rejected"]
        for start in range(0, len(aside), 400):
            self._store(values=aside[start:start + 400])
        if aside:
            self.log(f"{self.symbol}: {len(aside)} values of {', '.join(sorted(dropped))} set aside (repeats XBRL)")

    def _capped(self, spec: Spec) -> Spec:
        """normalize_spec, with any KPI the cap took off recorded for the run summary."""
        normalized = ai.normalize_spec(spec)
        kept = {(g.key, k.key) for g in normalized.groups for k in g.kpis}
        trimmed = [f"{g.key}.{k.key}" for g in spec.groups for k in g.kpis
                   if (g.key, k.key) not in kept and not any(k.key == other.key for other in
                                                              next((n.kpis for n in normalized.groups if n.kind == "metric"), []))]
        if trimmed:
            self.result.trimmed_kpis = sorted(set(self.result.trimmed_kpis) | set(trimmed))
            self.log(f"{self.symbol}: KPI cap left out {', '.join(trimmed[:10])}")
        return normalized

    def _maintain(self, spec: Spec, version: int, latest_quarter: list[FilingRef]) -> tuple[Spec, int] | None:
        """The reasoning reader audits the newest quarter against the list: KPIs the documents report that the list lacks, and KPIs the
        company stopped reporting. Additions only extend the list; existing keys never change meaning."""
        document = self._document(*latest_quarter)
        period_end = max(ref.period_end for ref in latest_quarter).isoformat()
        captured = sorted(
            f"- {v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']} ({v['group_label']} / {v['kpi_label']}) = {v['value']}"
            for v in self.values.values() if v["method"] == "ai" and abs(_days(v["period_end"], period_end)) <= 12
        )
        data = f"KPI list:\n{ai.kpi_lines(spec)}\n\nCaptured for this quarter:\n" + ("\n".join(captured) or "- nothing")
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.MAINTAIN_TASK, data),
                               "maintain", ai.Maintenance, thinking_first=True, model="flash")
            if getattr(self, "_owner_edited", False):
                # The owner decides what an edited list tracks: the audit may suggest additions, never retirements.
                answer = answer.model_copy(update={"retire": []})
            covered = [] if self.profile.foreign else self._current_xbrl_members()
            updated = apply_maintenance(spec, answer, covered)
            if not getattr(self, "_owner_edited", False):
                # The extractor's own repeats of an XBRL breakdown go, whether or not the audit changed anything else.
                cleaned = without_xbrl_repeats(updated or spec, covered)
                updated = cleaned if cleaned is not spec else updated
        except (AiResponseError, ControlError, ValueError) as error:
            self.result.errors.append(f"KPI list audit: {error}"[:300])
            return None
        if updated is None:
            return None
        updated = self._capped(updated)  # the audit's additions are kept to the cap like any list
        if updated.model_dump() == spec.model_dump():
            return None
        self._set_aside_dropped(spec, updated)
        self._store_spec(updated, self.names, version + 1, latest_quarter[-1].accession)
        self.log(f"{self.symbol}: KPI list v{version + 1} after audit ({sum(len(g.kpis) for g in updated.groups)} KPIs)")
        return updated, version + 1

    def _store_spec(self, spec: Spec, names: dict, version: int, accession: str) -> None:
        self.control.call("store", symbol=self.symbol, spec={
            "version": version, "groups": [group.model_dump() for group in spec.groups], "names": names,
            "sourceAccession": accession,
        })

    def _xbrl_breakdowns(self) -> dict[str, dict]:
        """Each XBRL breakdown's latest title and members (key → label)."""
        latest: dict[str, str] = {}
        for value in self.values.values():
            if value["method"] == "xbrl" and value["group_key"] != INCOME_GROUP and value["period_end"] >= latest.get(value["group_key"], ""):
                latest[value["group_key"]] = value["period_end"]
        breakdowns: dict[str, dict] = {}
        for value in sorted(self.values.values(), key=lambda item: item["kpi_order"]):
            if value["method"] == "xbrl" and value["period_end"] == latest.get(value["group_key"]):
                group = breakdowns.setdefault(value["group_key"], {"label": value["group_label"], "members": {}})
                group["members"][value["kpi_key"]] = value["kpi_label"]
        return breakdowns

    def _current_xbrl_members(self) -> list[set[str]]:
        """The member names of each XBRL breakdown the company still tags (its latest quarter within ~4 months of the
        newest XBRL figure): a list need not read these from releases. One the company stopped tagging (Meta's
        advertising and other revenue, to 2021) is not here, so its later quarters can come from releases."""
        newest = max((value["period_end"] for value in self.values.values() if value["method"] == "xbrl"), default=None)
        if newest is None:
            return []
        latest: dict[str, str] = {}
        for value in self.values.values():
            if value["method"] == "xbrl" and value["period_end"] >= latest.get(value["group_key"], ""):
                latest[value["group_key"]] = value["period_end"]
        return [{_identity_text(label) for label in group["members"].values()}
                for key, group in self._xbrl_breakdowns().items() if abs(_days(latest[key], newest)) <= 125]

    def _carry_owner_additions(self, spec: Spec, version: int, previous: dict | None, releases: list[FilingRef]) -> None:
        """When the owner only added KPIs (a new list version that keeps every KPI of the one before), older quarters
        keep their readings and move to the new version; only the last four quarters are read for the additions, as
        the audit's additions are. A run reaching past a refresh's window (the page's Read filings, ten years) reads the
        additions in every quarter it covers: a KPI's history (Meta's Facebook DAUs, reported to 2023)."""
        if not previous or int(previous.get("version") or 0) >= version or not releases or self.quarters > REFRESH_QUARTERS:
            return
        try:
            before = Spec.model_validate({"groups": previous.get("groups") or [], "names": {}})
        except ValueError:
            return
        if not only_adds(before, spec):
            return
        latest_end = max(ref.period_end for ref in releases if ref.period_end) if any(ref.period_end for ref in releases) else None
        if latest_end is None:
            return
        cutoff = latest_end - timedelta(days=370)
        carried = 0
        for ref in releases:
            filing = self.filings.get(ref.accession) or {}
            if ref.period_end and ref.period_end < cutoff and filing.get("spec_version") == int(previous["version"]):
                self._carry_to_version(ref, version)
                carried += 1
        if carried:
            self.log(f"{self.symbol}: owner added KPIs; {carried} older filings kept, the last four quarters read again")

    def _carry_to_version(self, ref: FilingRef, version: int) -> None:
        filing = self.filings.get(ref.accession)
        if not filing or filing.get("status") not in _DONE:
            return
        # A complete checkpoint: the request check requires the filing's identity, not only what changed.
        carried = {"accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(),
                   "document_role": ref.role, "source_url": ref.source_url, "period_end": filing.get("period_end"),
                   "spec_version": version, "status": filing["status"], "attempts": max(1, filing.get("attempts") or 1),
                   "value_count": filing.get("value_count", 0), "notes": filing.get("notes") or []}
        self._store(filings=[carried])
        self.filings[ref.accession] = {**filing, "spec_version": version}

    def _adopt_listed_keys(self, spec: Spec) -> None:
        """Stored rows of a table read whole move to the key of the listed KPI they are ("3-nanometer" → nm3), the key a
        row-by-row reading uses, so one quarter never holds a figure twice and years line up for Q4. No AI involved."""
        groups = {AI_GROUP_PREFIX + group.key: group for group in spec.groups}
        moved = []
        for v in list(self.values.values()):
            group = groups.get(v["group_key"])
            if group is None or v["method"] != "ai" or v["validation_status"] == "rejected":
                continue
            kpi = _listed_kpi(group, v["kpi_label"]) or _listed_kpi(group, v["kpi_key"])
            if kpi is None or kpi.key == v["kpi_key"]:
                continue
            target = self.values.get((v["group_key"], kpi.key, v["fiscal_year"], v["fiscal_period"]))
            if target and target["validation_status"] == "verified" and v["validation_status"] != "verified":
                moved.append({**v, "validation_status": "rejected", "notes": [f"same figure as {kpi.key}"]})
                continue
            moved.append({**v, "kpi_key": kpi.key, "kpi_label": kpi.label})
            moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {kpi.key}"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])
