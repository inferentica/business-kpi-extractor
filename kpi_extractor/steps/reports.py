"""10-Qs and 10-Ks read from XBRL: their breakdowns and each segment's operating income."""
from __future__ import annotations


from ..ops.control import ControlError
from ..documents.document import Document, clean, parse_document, parse_number
from ..domain.fiscal import fiscal_label
from ..readers.release_breakdowns import line_key
from ..sources.base import FilingRef
from ..store import FilingReading, failed_reading
from ..readers.segment_income import (GROUP_KEY as INCOME_GROUP, LEFT_OVER_KEY, REVENUE_GROUP_KEY as OWN_REVENUE_GROUP,
                              segment_income, segment_revenue)
from ..xbrl import extract_breakdowns, member_children, member_key, official_labels, presented_labels
from ..xbrl.basis import report_basis
from .shared import _XBRL_READER, value_key

_AI_PENDING = "AI review unavailable; retried next run"
# Segment operating income sorts after the revenue breakdowns, and the revenue-only steps (release and prospectus
# figures, the repeat guard) leave it out.
INCOME_ORDER = 50

def _row_named(document: Document, value: float, others: set[str]) -> str | None:
    """The label of a table row whose first amount is `value`, in a table that lists at least two of `others`."""
    for table in document.tables.values():
        scale = table.declared_scale() or document.default_scale()
        labels = [clean(row[0]) if row else "" for row in table.rows]
        if not scale or len({line_key(label) for label in labels if label} & others) < 2:
            continue
        for row, label in zip(table.rows, labels):
            if not label or line_key(label) in others:
                continue
            amount = next((parsed for cell in row[1:] if (parsed := parse_number(cell)) is not None), None)
            if amount is not None and not amount.percent and abs(amount.value * scale - value) <= scale / 2:
                return label
    return None



class ReportReading:
    """10-Qs and 10-Ks read from XBRL: their breakdowns and each segment's operating income."""

    def _read_report(self, ref: FilingRef) -> FilingReading | None:
        if not self.force and not self._pending(ref):
            return None
        try:
            xbrl = self._xbrl(ref)
            if xbrl is None:
                # Judged by this reader: stamped, so later runs leave it (it was re-read every run until attempts ran out).
                return FilingReading(ref, "skipped", notes=["no XBRL", _XBRL_READER])
            facts = xbrl.facts.to_dataframe()
            labels = official_labels(getattr(xbrl, "element_catalog", None))
            self._member_children = member_children(xbrl)
            breakdowns = extract_breakdowns(facts, xbrl.entity_info or {}, ref.form, labels, self._member_children)
            if breakdowns is None:
                return FilingReading(ref, "skipped", notes=["no document period", _XBRL_READER])
            fiscal_year, fiscal_period = fiscal_label(breakdowns.period_end, self.profile.fiscal_year_end,
                                                      annual=breakdowns.annual, year_offset=self.offset)
            self._store_statements(ref, xbrl, breakdowns, fiscal_year, fiscal_period)
            # A company's own segment revenue measure is its revenue by segment, in place of the GAAP split of it.
            try:
                own_revenue = segment_revenue(facts, breakdowns.period_end, breakdowns.annual, labels, self._member_children)
            except Exception:  # noqa: BLE001 - the GAAP split stands
                own_revenue = None
            groups = [(group, []) for group in breakdowns.groups if not (own_revenue and group.key == OWN_REVENUE_GROUP)]
            taken = {group.key for group, _notes in groups} | ({OWN_REVENUE_GROUP} if own_revenue else set())
            ai_pending = False
            for candidate in breakdowns.rejected:
                if candidate.key in taken:
                    continue
                try:
                    group = self._curate(candidate)
                except ControlError:
                    # The AI is unavailable (e.g. out of credit): keep every reconciled breakdown now and let the
                    # next run retry the review, instead of losing the whole filing.
                    ai_pending = True
                    continue
                if group is not None:
                    groups.append((group, ["rows chosen by review"]))
                    taken.add(group.key)
            groups = [(self._continuing_split(group, breakdowns.period_end), notes) for group, notes in groups]
            records = []
            for group, extra_notes in groups:
                named = self.names.get(group.key) or {}
                # Rows that land on one key ("Other" and "All other") are parts of it: they add up.
                # Their nine months to date add up the same way, or Q4 = year − nine months would be wrong.
                merged: dict[str, list] = {}
                for reported, label, value in group.members:
                    # The reader already summed each key's nine-month figures; take them once per key.
                    if reported in merged:
                        merged[reported][2] += value
                    else:
                        merged[reported] = [reported, label, value, group.year_to_date.get(reported),
                                            group.prior_year_to_date.get(reported)]
                for order, (member, (reported, label, value, ytd, prior_ytd)) in enumerate(merged.items()):
                    records.append(self._record(
                        group_key=group.key, group_label=named.get("label") or group.label, group_kind="revenue_breakdown",
                        group_order=group.order, kpi_key=member, kpi_label=(named.get("members") or {}).get(member) or label,
                        kpi_order=order, unit="currency", value=value, currency=group.currency, method="xbrl",
                        validation_status="verified", reconciliation_error_pct=group.reconciliation_error,
                        notes=extra_notes, ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                        period_end=breakdowns.period_end, locator={
                            "concept": group.concept, "total": group.total, "member": group.elements.get(reported),
                            **({"parent": group.parents[reported]} if reported in group.parents else {}),
                            "prior": group.prior.get(reported),
                            **({"prior_total": group.prior_total} if group.prior_total else {}),
                            **({"ytd": ytd, "ytd_total": group.year_to_date_total}
                               if ytd is not None and group.year_to_date_total else {}),
                            **({"prior_ytd": prior_ytd, "prior_ytd_total": group.prior_year_to_date_total}
                               if prior_ytd is not None and group.prior_year_to_date_total else {}),
                        },
                    ))
            records = self._name_remainders(ref, records)
            # A split's subtotals (Alphabet's Google properties, Google advertising) in a group of their own: placed in the
            # tree by their parents, never added into a breakdown's sum.
            for group, extra_notes in groups:
                named = self.names.get(group.key) or {}
                for order, (member, label, value) in enumerate(group.levels):
                    ytd = group.level_year_to_date.get(member)
                    records.append(self._record(
                        group_key=f"{group.key}_levels", group_label=f"{named.get('label') or group.label} subtotals",
                        group_kind="metric", group_order=group.order, kpi_key=member,
                        kpi_label=(named.get("members") or {}).get(member) or label, kpi_order=order, unit="currency",
                        value=value, currency=group.currency, method="xbrl", validation_status="verified",
                        reconciliation_error_pct=None, notes=extra_notes, ref=ref, fiscal_year=fiscal_year,
                        fiscal_period=fiscal_period, period_end=breakdowns.period_end, locator={
                            "concept": group.concept, "member": group.elements.get(member), "subtotal": True,
                            "parent": group.parents.get(member), "prior": group.level_prior.get(member),
                            **({"ytd": ytd, "ytd_total": group.year_to_date_total} if ytd is not None and group.year_to_date_total else {}),
                        },
                    ))
            records += self._income_records(facts, labels, breakdowns, ref, fiscal_year, fiscal_period, groups, own_revenue,
                                            presented_labels(getattr(xbrl, "element_catalog", None)))
            if records:
                # The release's figures for this quarter give way to the report's; a line the report does not give
                # (renamed, or not broken out) stops being served.
                fresh = {value_key(record) for record in records}
                records += [{**value, "validation_status": "rejected", "notes": ["replaced by the quarter's report"]}
                            for value in self.values.values()
                            if (value.get("locator") or {}).get("release_table") and value["validation_status"] != "rejected"
                            and (value["fiscal_year"], value["fiscal_period"]) == (fiscal_year, fiscal_period)
                            and value_key(value) not in fresh]
            status = "failed" if ai_pending else "processed" if records else "skipped"
            notes = [_XBRL_READER, *([_AI_PENDING] if ai_pending else [] if records
                                     else ["no revenue breakdown reconciles to reported revenue"])]
            standard, currency = report_basis(facts)
            return FilingReading(ref, status, records=records, period_end=breakdowns.period_end,
                                 fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes,
                                 accounting_standard=standard, currency=currency)
        except Exception as error:  # noqa: BLE001 - one bad filing must not stop the company
            return failed_reading(ref, error)

    def _name_remainders(self, ref: FilingRef, records: list[dict]) -> list[dict]:
        """A breakdown's worked-out remainder under the name the report's own table gives it: Salesforce's Q2 FY2027
        10-Q tags no "Hedging gains", which its tables list beside its regions and its service offerings. The row must
        sit in a table with at least two of the breakdown's other lines and equal the remainder to the table's rounding."""
        left = [record for record in records if record["kpi_key"] == LEFT_OVER_KEY]
        if not left:
            return records
        try:
            document = parse_document(self.source.report_html(ref, self.archive), ref.source_url)
        except Exception:  # noqa: BLE001 - the remainder keeps its worked-out name
            return records
        names: dict[int, str] = {}
        for row in left:
            others = {line_key(record["kpi_label"]) for record in records if record["group_key"] == row["group_key"]
                      and record is not row}
            if name := _row_named(document, float(row["value"]), others):
                names[id(row)] = name
        return [{**record, "kpi_key": member_key(name), "kpi_label": name} if (name := names.get(id(record))) else record
                for record in records]

    def _income_records(self, facts, labels, breakdowns, ref: FilingRef, fiscal_year: str, fiscal_period: str,
                        groups, own_revenue=None, line_labels=None) -> list[dict]:
        """Each segment's operating income, as tagged (see segment_income): its own breakdown, beside the revenue ones; and
        a company's own segment revenue measure, which its segment profit is measured against."""
        try:
            income = segment_income(facts, breakdowns.period_end, breakdowns.annual, labels, self._member_children, line_labels)
        except Exception as error:  # noqa: BLE001 - the revenue breakdowns stand without it
            self.log(f"{self.symbol}: segment income of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
            return []
        records = self._measure_records(own_revenue, OWN_REVENUE_GROUP, 0, groups, ref, fiscal_year,
                                        fiscal_period, breakdowns.period_end) if own_revenue else []
        if income is None:
            return records
        prior = {key: value for key, _name, _element, value in income.prior.rows} if income.prior else {}
        # A year whose worked-out remainder is the one line its quarters name (AMD's 10-K tags "All Other" with the sign
        # its 10-Qs give it reversed, so nothing tagged closes the gap) keeps that name.
        keys = {key for key, *_rest in income.current.rows}
        quarters = [v for v in self.values.values() if v["group_key"] == INCOME_GROUP and v["validation_status"] == "verified"
                    and v["fiscal_year"] == fiscal_year and v["fiscal_period"] in ("Q1", "Q2", "Q3")]
        quarter_names = {v["kpi_key"]: v["kpi_label"] for v in quarters}
        # So does a year whose 10-K itemizes what its 10-Qs give as that one line (AMD's 2023 "All Other"): its Q4 is the
        # year less the quarters, line by line.
        apart = keys - set(quarter_names)
        if breakdowns.annual and len({v["fiscal_period"] for v in quarters}) == 3 and apart and len(set(quarter_names) - keys) == 1 \
                and apart <= {LEFT_OVER_KEY, *income.current.lines}:
            other = next(iter(set(quarter_names) - keys))
            whole = sum(value for key, _label, _element, value in income.current.rows if key in apart)
            income.current.rows = [(key, label, element, value) for key, label, element, value in income.current.rows
                                   if key not in apart] + [(other, quarter_names[other], None, whole)]
            income.current.lines = set()
            if apart <= set(prior):
                prior = {key: value for key, value in prior.items() if key not in apart} | {other: sum(prior[key] for key in apart)}
        records += self._measure_records(income, INCOME_GROUP, INCOME_ORDER, groups, ref, fiscal_year, fiscal_period,
                                         breakdowns.period_end, prior=prior)
        # The rows this report gives are the period's: one an earlier read stored under another name (a worked-out
        # "Unallocated" whose lines are now named, Palantir's "Government Operating") would count twice.
        # A remainder no tag names takes the report's own row first (Broadcom's "Unallocated expenses").
        records = self._name_remainders(ref, records)
        fresh = {record["kpi_key"] for record in records if record["group_key"] == INCOME_GROUP}
        return records + [{**value, "validation_status": "rejected", "notes": ["replaced by the report's own lines"]}
                          for value in self.values.values()
                          if value["group_key"] == INCOME_GROUP and value["method"] == "xbrl" and value["kpi_key"] not in fresh
                          and value["validation_status"] != "rejected"
                          and (value["fiscal_year"], value["fiscal_period"]) == (fiscal_year, fiscal_period)]

    def _retire_named_remainders(self) -> list[dict]:
        """A period whose year-ago lines now name what its worked-out "Unallocated" stood for keeps only the lines: the
        rows it was stored with would otherwise count the same costs twice. Only when the lines equal it."""
        periods: dict[tuple[str, str], list[dict]] = {}
        for value in self.values.values():
            if value["group_key"] == INCOME_GROUP and value["validation_status"] != "rejected" and value["method"] == "xbrl":
                periods.setdefault((value["fiscal_year"], value["fiscal_period"]), []).append(value)
        retired = []
        for rows in periods.values():
            left = next((row for row in rows if row["kpi_key"] == LEFT_OVER_KEY), None)
            lines = [row for row in rows if (row.get("locator") or {}).get("line")]
            if left and lines and abs(float(left["value"]) - sum(float(row["value"]) for row in lines)) <= max(abs(float(left["value"])) * 0.001, 1.0):
                retired.append({**left, "validation_status": "rejected", "notes": ["named by the report's own lines"]})
        return retired

    def _measure_records(self, measure, group_key: str, group_order: int, groups, ref: FilingRef, fiscal_year: str,
                         fiscal_period: str, period_end, prior: dict | None = None) -> list[dict]:
        """A segment measure's rows (see segment_income), with the year before and the year to date in their locators."""
        currency = next((group.currency for group, _notes in groups if group.currency), None) or "USD"
        named = self.names.get(group_key) or {}
        ytd = {key: value for key, _name, _element, value in measure.year_to_date.rows} if measure.year_to_date else {}
        prior = prior if prior is not None else (
            {key: value for key, _name, _element, value in measure.prior.rows} if measure.prior else {})
        records = []
        for order, (key, label, element, value) in enumerate(measure.current.rows):
            records.append(self._record(
                group_key=group_key, group_label=named.get("label") or measure.label, group_kind="revenue_breakdown",
                group_order=group_order, kpi_key=key, kpi_label=(named.get("members") or {}).get(key) or label,
                kpi_order=order, unit="currency", value=value, currency=currency, method="xbrl", validation_status="verified",
                reconciliation_error_pct=0, notes=[], ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                period_end=period_end, locator={
                    "concept": measure.concept, "total": measure.current.total, "member": element,
                    **({"line": True} if key in measure.current.lines else {}),
                    **({"prior": prior[key], "prior_total": measure.prior.total} if key in prior else {}),
                    **({"ytd": ytd[key], "ytd_total": measure.year_to_date.total} if key in ytd else {}),
                },
            ))
        return records
