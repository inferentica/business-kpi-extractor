"""A recent listing's history: its first reports' year-ago figures and its IPO prospectus."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from ..ai import Spec
from ..domain.fiscal import fiscal_label
from ..readers.prospectus import interim_quarters, prospectus_breakdowns, quarter_sections
from ..readers.release_breakdowns import line_key
from ..sources.sec import FilingRef, ipo_prospectus
from ..readers.segment_income import GROUP_KEY as INCOME_GROUP
from ..store import FilingReading
from .shared import AI_GROUP_PREFIX, _DONE

_PROSPECTUS_READER = "prospectus reader 1"


class ListingHistory:
    """A recent listing's history: its first reports' year-ago figures and its IPO prospectus."""

    def _prospectus(self, company, reports: list[FilingRef]) -> FilingRef | None:
        try:
            return ipo_prospectus(company, min((ref.filed for ref in reports), default=None))
        except Exception as error:  # noqa: BLE001 - the history is extra; the company is read without it
            self.log(f"{self.symbol}: prospectus not found ({type(error).__name__}: {error})"[:300])
            return None

    def _xbrl_groups(self) -> dict[str, list[dict]]:
        """Each XBRL breakdown's lines in its latest quarter (subtotal groups left out)."""
        latest: dict[str, tuple[str, str]] = {}
        rows = [value for value in self.values.values() if value["method"] == "xbrl" and value["validation_status"] == "verified"
                and value["fiscal_period"] != "FY" and not value["group_key"].endswith("_levels")
                and not value["group_key"].startswith(AI_GROUP_PREFIX) and value["group_key"] != INCOME_GROUP]
        for value in rows:
            latest[value["group_key"]] = max(latest.get(value["group_key"], ("", "")), (value["fiscal_year"], value["fiscal_period"]))
        groups: dict[str, list[dict]] = {}
        for value in rows:
            if (value["fiscal_year"], value["fiscal_period"]) == latest[value["group_key"]]:
                groups.setdefault(value["group_key"], []).append(value)
        return groups

    def _held(self, group: str, quarter: tuple[str, str]) -> bool:
        return any(value["group_key"] == group and (value["fiscal_year"], value["fiscal_period"]) == quarter
                   and value["validation_status"] != "rejected" for value in self.values.values())

    def _year_ago_quarters(self) -> list[dict]:
        """A company's first reports give each breakdown's year-ago quarter (SpaceX's Q2 2026 10-Q, Q2 2025): kept when
        every line has one and they add up to the year-ago total, for a quarter the company never reported."""
        records: list[dict] = []
        by_quarter: dict[tuple[str, str, str], list[dict]] = {}
        for value in self.values.values():
            # Years too: a first 10-K's comparative year lets the year before's Q4 be worked out from its quarters.
            if (value["method"] == "xbrl" and value["validation_status"] == "verified"
                    and not value["group_key"].endswith("_levels") and not value["group_key"].startswith(AI_GROUP_PREFIX)):
                by_quarter.setdefault((value["group_key"], value["fiscal_year"], value["fiscal_period"]), []).append(value)
        for (group, year, period), lines in by_quarter.items():
            before = (str(int(year) - 1), period)
            priors = [(line.get("locator") or {}).get("prior") for line in lines]
            total = next((t for line in lines if isinstance(t := (line.get("locator") or {}).get("prior_total"), (int, float))), None)
            if self._held(group, before) or total is None or not all(isinstance(prior, (int, float)) for prior in priors):
                continue
            if abs(sum(priors) - total) > abs(total) * 0.001:
                continue
            end = date.fromisoformat(str(lines[0]["period_end"])[:10])
            end = end.replace(year=end.year - 1, day=min(end.day, 28 if end.month == 2 else end.day))
            for line, prior in zip(lines, priors):
                records.append({**line, "fiscal_year": before[0], "period_end": end.isoformat(), "value": float(prior),
                                "reconciliation_error_pct": (sum(priors) - total) / total if total else None,
                                "notes": [f"the year-ago figure in the report for {period} {year}"],
                                "locator": {"total": total, "member": (line.get("locator") or {}).get("member"), "year_ago": True,
                                            **({"line": True} if (line.get("locator") or {}).get("line") else {})}})
        if records:
            self.log(f"{self.symbol}: {len(records)} year-ago values from the first reports' comparatives")
        return records

    def _read_prospectus_breakdowns(self, ref: FilingRef) -> tuple[FilingReading | None, dict[tuple[str, str], str]]:
        """Each XBRL breakdown for every period the prospectus's tables give it, for periods the company never reported
        (see prospectus.py). Read once; the full years let the last pre-listing Q4 be worked out. Also gives the lines the
        prospectus calls by another name (group, its name) → today's key."""
        previous = self.filings.get(ref.accession) or {}
        if previous.get("status") in _DONE and _PROSPECTUS_READER in (previous.get("notes") or []):
            return None, {}
        try:
            document = self._document(ref)
            records: list[dict] = []
            renamed: dict[tuple[str, str], str] = {}  # (group, the prospectus's name) → the line it is today
            for group, lines in self._xbrl_groups().items():
                reference = next((t for line in lines if isinstance(t := (line.get("locator") or {}).get("total"), (int, float))), None)
                if not reference:
                    continue
                members = {line["kpi_key"]: line["kpi_label"] for line in lines}
                templates = {line["kpi_key"]: line for line in lines}
                for period, found in prospectus_breakdowns(document, members, float(reference)).items():
                    for member, label in found.rows.items():
                        if line_key(label) not in (member, line_key(templates[member]["kpi_label"])):
                            renamed[(group, line_key(label))] = member
                    try:
                        quarter = fiscal_label(period.end, self.profile.fiscal_year_end, annual=period.annual, year_offset=self.offset)
                    except Exception:  # noqa: BLE001 - a period off the company's calendar
                        continue
                    if self._held(group, quarter):
                        continue
                    for member, value in found.values.items():
                        template = templates[member]
                        records.append(self._record(
                            group_key=group, group_label=template["group_label"], group_kind=template["group_kind"],
                            group_order=template["group_order"], kpi_key=member, kpi_label=template["kpi_label"],
                            kpi_order=template["kpi_order"], unit="currency", value=value, currency=template.get("currency"),
                            method="xbrl", validation_status="verified",
                            reconciliation_error_pct=(sum(found.values.values()) - found.total) / found.total if found.total else None,
                            notes=["from the IPO prospectus"], ref=ref, fiscal_year=quarter[0], fiscal_period=quarter[1],
                            period_end=period.end, locator={"total": found.total, "member": (template.get("locator") or {}).get("member"),
                                                            "prospectus": True, "table": found.table, "row": found.rows.get(member)},
                        ))
            return FilingReading(ref, "processed" if records else "skipped", records=records,
                                 notes=[_PROSPECTUS_READER, f"{len(records)} breakdown figures from the IPO prospectus"]), renamed
        except Exception as error:  # noqa: BLE001 - the history is extra
            self.log(f"{self.symbol}: prospectus breakdowns not read ({type(error).__name__}: {error})"[:300])
            return None, {}

    def _join_prospectus_names(self, renamed: dict[tuple[str, str], str]) -> list[dict]:
        """A line renamed after listing is one series under today's name: the prospectus's row the line was matched to
        (SpaceX's "AI", today "SI") names the reports' earlier key for it, when the two never share a quarter."""
        records = []
        for (group, old), new in renamed.items():
            rows = [v for v in self.values.values() if v["group_key"] == group and v["validation_status"] != "rejected"]
            before = [v for v in rows if v["kpi_key"] == old]
            current = [v for v in rows if v["kpi_key"] == new]
            if not before or not current or {(v["fiscal_year"], v["fiscal_period"]) for v in before} & {
                    (v["fiscal_year"], v["fiscal_period"]) for v in current}:
                continue
            for value in before:
                records.append({**value, "kpi_key": new, "kpi_label": current[0]["kpi_label"],
                                "notes": [*(value.get("notes") or []), f"named {value['kpi_label']} then"]})
                records.append({**value, "validation_status": "rejected", "notes": [f"continued as {current[0]['kpi_label']}"]})
        if records:
            self.log(f"{self.symbol}: {len(records) // 2} values joined to the line they were renamed to")
        return records

    def _read_prospectus_quarters(self, ref: FilingRef, releases: list[FilingRef], spec: Spec, version: int) -> None:
        """The KPI list read from the prospectus, as from a release, for its latest interim quarter and the year before
        (SpaceX's Starlink subscribers, launches and mass to orbit for Q1 2026 and Q1 2025), when no release gave them.
        Each quarter is its own reading of the prospectus, recorded once."""
        try:
            document = self._document(ref)
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: prospectus not read ({type(error).__name__}: {error})"[:300])
            return
        covered = {ref.period_end for ref in releases if ref.period_end}
        for end in interim_quarters(document)[:2]:
            if any(abs((end - other).days) <= 10 for other in covered):
                continue
            year, period = fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
            quarter = replace(ref, accession=f"{ref.accession}-{year}{period}", period_end=end)
            if (self.filings.get(quarter.accession) or {}).get("status") in _DONE:
                continue
            labels = [kpi.label for group in spec.groups for kpi in group.kpis]
            self._documents[quarter.accession] = quarter_sections(document, labels, end)
            self._read_and_record_release(quarter, spec, version)
